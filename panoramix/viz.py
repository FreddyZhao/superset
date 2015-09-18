from collections import OrderedDict
from datetime import datetime
from urllib import urlencode
import uuid

from flask import flash
from werkzeug.datastructures import MultiDict
from werkzeug.urls import Href
import numpy as np
import pandas as pd

from panoramix import utils, config
from panoramix.highchart import Highchart, HighchartBubble
from panoramix.forms import form_factory

CHART_ARGS = {
    'title': None,
}


class BaseViz(object):
    verbose_name = "Base Viz"
    template = None
    hidden_fields = []
    form_fields = [
        'viz_type', 'metrics', 'groupby', 'granularity',
        ('since', 'until')]
    js_files = []
    css_files = []

    def __init__(self, datasource, form_data):
        self.datasource = datasource
        if isinstance(form_data, MultiDict):
            self.args = form_data.to_dict(flat=True)
        else:
            self.args = form_data
        self.form_data = form_data
        self.token = self.args.get('token', 'token_' + uuid.uuid4().hex[:8])

        as_list = ('metrics', 'groupby')
        d = self.args
        for m in as_list:
            if m in d and d[m] and not isinstance(d[m], list):
                d[m] = [d[m]]
        self.metrics = self.args.get('metrics') or ['count']
        self.groupby = self.args.get('groupby') or []

    def get_url(self, **kwargs):
        d = self.args.copy()
        d.update(kwargs)
        href = Href('/panoramix/table/2/')
        href = Href(
            '/panoramix/{self.datasource.type}/'
            '{self.datasource.id}/'.format(**locals()))
        return href(d)

    def get_df(self):
        self.error_msg = ""
        self.results = None

        #try:
        self.results = self.bake_query()
        df = self.results.df
        if df is not None:
            if 'timestamp' in df.columns:
                df.timestamp = pd.to_datetime(df.timestamp)
        return df
        '''
        except Exception as e:
            logging.exception(e)
            self.error_msg = str(e)
        '''

    @property
    def form(self):
        return self.form_class(self.form_data)

    @property
    def form_class(self):
        return form_factory(self)

    def query_filters(self):
        args = self.args
        # Building filters
        filters = []
        for i in range(1, 10):
            col = args.get("flt_col_" + str(i))
            op = args.get("flt_op_" + str(i))
            eq = args.get("flt_eq_" + str(i))
            if col and op and eq:
                filters.append((col, op, eq))
        return filters

    def bake_query(self):
        return self.datasource.query(**self.query_obj())

    def query_obj(self):
        """
        Building a query object
        """
        args = self.args
        groupby = args.get("groupby") or []
        metrics = args.get("metrics") or ['count']
        granularity = args.get("granularity", "1 day")
        if granularity != "all":
            granularity = utils.parse_human_timedelta(
                granularity).total_seconds() * 1000
        limit = int(args.get("limit", 0))
        row_limit = int(
            args.get("row_limit", config.ROW_LIMIT))
        since = args.get("since", "1 year ago")
        from_dttm = utils.parse_human_datetime(since)
        if from_dttm > datetime.now():
            from_dttm = datetime.now() - (from_dttm-datetime.now())
        until = args.get("until", "now")
        to_dttm = utils.parse_human_datetime(until)
        if from_dttm >= to_dttm:
            flash("The date range doesn't seem right.", "danger")
            from_dttm = to_dttm  # Making them identical to not raise

        # extras are used to query elements specific to a datasource type
        # for instance the extra where clause that applies only to Tables
        extras = {
            'where': args.get("where", '')
        }
        d = {
            'granularity': granularity,
            'from_dttm': from_dttm,
            'to_dttm': to_dttm,
            'is_timeseries': True,
            'groupby': groupby,
            'metrics': metrics,
            'row_limit': row_limit,
            'filter': self.query_filters(),
            'timeseries_limit': limit,
            'extras': extras,
        }
        return d


class TableViz(BaseViz):
    verbose_name = "Table View"
    template = 'panoramix/viz_table.html'
    form_fields = BaseViz.form_fields + ['row_limit']
    css_files = ['dataTables.bootstrap.css']
    js_files = ['jquery.dataTables.min.js', 'dataTables.bootstrap.js']

    def query_obj(self):
        d = super(TableViz, self).query_obj()
        d['is_timeseries'] = False
        d['timeseries_limit'] = None
        return d

    def get_df(self):
        df = super(TableViz, self).get_df()
        if (
                self.form_data.get("granularity") == "all" and
                'timestamp' in df):
            del df['timestamp']
        for m in self.metrics:
            df[m + '__perc'] = np.rint((df[m] / np.max(df[m])) * 100)
        return df


class HighchartsViz(BaseViz):
    verbose_name = "Base Highcharts Viz"
    template = 'panoramix/viz_highcharts.html'
    chart_kind = 'line'
    chart_call = "Chart"
    stacked = False
    compare = False
    js_files = ['highstock.js']


class BubbleViz(HighchartsViz):
    verbose_name = "Bubble Chart"
    chart_type = 'bubble'
    hidden_fields = ['granularity', 'metrics', 'groupby']
    form_fields = [
        'viz_type', 'since', 'until',
        'series', 'entity', 'x', 'y', 'size', 'limit']
    js_files = ['highstock.js', 'highcharts-more.js']

    def query_obj(self):
        args = self.form_data
        d = super(BubbleViz, self).query_obj()
        d['granularity'] = 'all'
        d['groupby'] = list({
            args.get('series'),
            args.get('entity')
        })
        self.x_metric = args.get('x')
        self.y_metric = args.get('y')
        self.z_metric = args.get('size')
        self.entity = args.get('entity')
        self.series = args.get('series')
        d['metrics'] = [
            self.z_metric,
            self.x_metric,
            self.y_metric,
        ]
        if not all(d['metrics'] + [self.entity, self.series]):
            raise Exception("Pick a metric for x, y and size")
        return d

    def get_df(self):
        df = super(BubbleViz, self).get_df()
        df = df.fillna(0)
        df['x'] = df[[self.x_metric]]
        df['y'] = df[[self.y_metric]]
        df['z'] = df[[self.z_metric]]
        df['name'] = df[[self.entity]]
        df['group'] = df[[self.series]]
        return df

    def get_json(self):
        df = self.get_df()
        chart = HighchartBubble(df)
        return chart.json


class TimeSeriesViz(HighchartsViz):
    verbose_name = "Time Series - Line Chart"
    chart_type = "spline"
    chart_call = "StockChart"
    sort_legend_y = True
    js_files = ['highstock.js', 'highcharts-more.js']
    form_fields = [
        'viz_type',
        'granularity', ('since', 'until'),
        'metrics',
        'groupby', 'limit',
        ('rolling_type', 'rolling_periods'),
    ]

    def get_df(self):
        args = self.args
        df = super(TimeSeriesViz, self).get_df()
        metrics = self.metrics
        df = df.pivot_table(
            index="timestamp",
            columns=self.groupby,
            values=metrics,)

        rolling_periods = args.get("rolling_periods")
        rolling_type = args.get("rolling_type")
        if rolling_periods and rolling_type:
            if rolling_type == 'mean':
                df = pd.rolling_mean(df, int(rolling_periods))
            elif rolling_type == 'std':
                df = pd.rolling_std(df, int(rolling_periods))
            elif rolling_type == 'sum':
                df = pd.rolling_sum(df, int(rolling_periods))
        return df

    def get_json(self):
        df = self.get_df()
        chart = Highchart(
            df,
            compare=self.compare,
            chart_type=self.chart_type,
            stacked=self.stacked,
            sort_legend_y=self.sort_legend_y,
            **CHART_ARGS)
        return chart.json


class TimeSeriesCompareViz(TimeSeriesViz):
    verbose_name = "Time Series - Percent Change"
    compare = 'percent'


class TimeSeriesCompareValueViz(TimeSeriesViz):
    verbose_name = "Time Series - Value Change"
    compare = 'value'


class TimeSeriesAreaViz(TimeSeriesViz):
    verbose_name = "Time Series - Stacked Area Chart"
    stacked = True
    chart_type = "area"


class TimeSeriesBarViz(TimeSeriesViz):
    verbose_name = "Time Series - Bar Chart"
    chart_type = "column"


class TimeSeriesStackedBarViz(TimeSeriesViz):
    verbose_name = "Time Series - Stacked Bar Chart"
    chart_type = "column"
    stacked = True




class DistributionPieViz(HighchartsViz):
    verbose_name = "Distribution - Pie Chart"
    chart_type = "pie"
    js_files = ['highstock.js']
    form_fields = BaseViz.form_fields + ['limit']

    def query_obj(self):
        d = super(DistributionPieViz, self).query_obj()
        d['granularity'] = "all"
        d['is_timeseries'] = False
        return d

    def get_df(self):
        df = super(DistributionPieViz, self).get_df()
        df = df.pivot_table(
            index=self.groupby,
            values=[self.metrics[0]])
        df = df.sort(self.metrics[0], ascending=False)
        return df

    def get_json(self):
        df = self.get_df()
        chart = Highchart(
            df, chart_type=self.chart_type, **CHART_ARGS)
        self.chart_js = chart.javascript_cmd
        return chart.json


class DistributionBarViz(DistributionPieViz):
    verbose_name = "Distribution - Bar Chart"
    chart_type = "column"


viz_types = OrderedDict([
    ['table', TableViz],
    ['line', TimeSeriesViz],
    ['compare', TimeSeriesCompareViz],
    ['compare_value', TimeSeriesCompareValueViz],
    ['area', TimeSeriesAreaViz],
    ['bar', TimeSeriesBarViz],
    ['stacked_ts_bar', TimeSeriesStackedBarViz],
    ['dist_bar', DistributionBarViz],
    ['pie', DistributionPieViz],
    ['bubble', BubbleViz],
])