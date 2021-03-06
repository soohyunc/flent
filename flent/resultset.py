## -*- coding: utf-8 -*-
##
## resultset.py
##
## Author:   Toke Høiland-Jørgensen (toke@toke.dk)
## Date:     24 November 2012
## Copyright (c) 2012-2015, Toke Høiland-Jørgensen
##
## This program is free software: you can redistribute it and/or modify
## it under the terms of the GNU General Public License as published by
## the Free Software Foundation, either version 3 of the License, or
## (at your option) any later version.
##
## This program is distributed in the hope that it will be useful,
## but WITHOUT ANY WARRANTY; without even the implied warranty of
## MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
## GNU General Public License for more details.
##
## You should have received a copy of the GNU General Public License
## along with this program.  If not, see <http://www.gnu.org/licenses/>.

from __future__ import absolute_import, division, print_function, unicode_literals

import json, os, math, re, sys
from datetime import datetime
from calendar import timegm
from itertools import repeat
from copy import deepcopy

try:
    from dateutil.parser import parse as parse_date
except ImportError:
    from .util import parse_date

try:
    from collections import OrderedDict
except ImportError:
    from .ordereddict import OrderedDict

from .util import gzip_open, bz2_open

# Controls pretty-printing of json dumps
JSON_INDENT=2

__all__ = ['new', 'load']

RECORDED_SETTINGS = (
    "NAME",
    "HOST",
    "HOSTS",
    "TIME",
    "LOCAL_HOST",
    "TITLE",
    "NOTE",
    "LENGTH",
    "TOTAL_LENGTH",
    "STEP_SIZE",
    "TEST_PARAMETERS",
    "FLENT_VERSION",
    "IP_VERSION",
    "BATCH_NAME",
    "BATCH_TIME",
    "DATA_FILENAME",
    "HTTP_GETTER_URLLIST",
    "HTTP_GETTER_DNS",
    "HTTP_GETTER_WORKERS",
    )

FILEFORMAT_VERSION=2
SUFFIX = '.flent.gz'

# Time settings will be serialised as ISO timestamps and stored in memory as
# datetime instances
TIME_SETTINGS = ("TIME", "BATCH_TIME", "T0")

def new(settings):
    d = {}
    for a in RECORDED_SETTINGS:
        d[a] = deepcopy(getattr(settings,a,None))
    return ResultSet(**d)

def load(filename, absolute=False):
    return ResultSet.load_file(filename, absolute)

class ResultSet(object):
    def __init__(self, SUFFIX=SUFFIX, **kwargs):
        self._x_values = []
        self._results = OrderedDict()
        self._filename = None
        self._absolute = False
        self._raw_values = {}
        self.metadata = kwargs
        self.SUFFIX = SUFFIX
        if not 'TIME' in self.metadata or self.metadata['TIME'] is None:
            self.metadata['TIME'] = datetime.now()
        if not 'NAME' in self.metadata or self.metadata['NAME'] is None:
            raise RuntimeError("Missing name for resultset")
        if not 'DATA_FILENAME' in self.metadata or self.metadata['DATA_FILENAME'] is None:
            self.metadata['DATA_FILENAME'] = self.dump_file
        if not self.metadata['DATA_FILENAME'].endswith(self.SUFFIX):
            self.metadata['DATA_FILENAME'] += self.SUFFIX
        self._filename = self.metadata['DATA_FILENAME']

    def meta(self, k=None, v=None):
        if k:
            if v:
                self.metadata[k] = v
            return self.metadata[k]
        return self.metadata

    def label(self):
        return self.metadata["TITLE"] or self.metadata["TIME"].strftime("%Y-%m-%d %H:%M:%S")

    def get_x_values(self):
        return self._x_values
    def set_x_values(self, x_values):
        assert not self._x_values
        self._x_values = list(x_values)
    x_values = property(get_x_values, set_x_values)

    def add_result(self, name, data):
        assert len(data) == len(self._x_values)
        self._results[name] = data

    def add_raw_values(self, name, data):
        self._raw_values[name] = data

    def set_raw_values(self, raw_values):
        self._raw_values = raw_values

    def get_raw_values(self):
        return self._raw_values

    raw_values = property(get_raw_values, set_raw_values)

    def create_series(self, series_names):
        for n in series_names:
            self._results[n] = []

    def append_datapoint(self, x, data):
        """Append a datapoint to each series. Missing data results in append
        None (keeping all result series synchronised in x values).

        Requires preceding call to create_series() with the data series name(s).
        """
        data = dict(data)
        self._x_values.append(x)
        for k in list(self._results.keys()):
            if k in data:
                self._results[k].append(data[k])
                del data[k]
            else:
                self._results[k].append(None)

        if data:
            raise RuntimeError("Unexpected data point(s): %s" % list(data.keys()))

    def concatenate(self, res):
        if self._absolute:
            x0 = 0.0
            # When concatenating using absolute values, insert an empty data
            # point midway between the data series, to prevent the lines for
            # each distinct data series from being joined together.
            xnext = (self.x_values[-1] + res.x_values[0])/2.0
            self.append_datapoint(xnext, zip(res.series_names, repeat(None)))
        else:
            x0 = self.x_values[-1] + self.meta("STEP_SIZE")
        for point in res:
            x = point[0] + x0
            data = dict(zip(res.series_names, point[1:]))
            self.append_datapoint(x, data)

    def last_datapoint(self, series):
        data = self.series(series)
        if not data:
            return None
        return data[-1]

    def series(self, name, smooth=None):
        if not name in self._results:
            sys.stderr.write("Warning: Missing data points for series '%s'\n" % name)
            return [None]*len(self.x_values)
        if smooth:
            return self.smoothed(name, smooth)
        return self._results[name]

    def __getitem__(self, name):
        return self.series(name)

    def __contains__(self, name):
        return name in self._results

    def smoothed(self, name, amount):
        res = self._results[name]
        smooth_res = []
        for i in range(len(res)):
            s = int(max(0,i-amount/2))
            e = int(min(len(res),i+amount/2))
            window = [j for j in res[s:e] if j is not None]
            if window and res[i] is not None:
                smooth_res.append(math.fsum(window)/len(window))
            else:
                smooth_res.append(None)
        return smooth_res


    @property
    def series_names(self):
        return list(self._results.keys())

    def zipped(self, keys=None):
        if keys is None:
            keys = self.series_names
        for i in range(len(self._x_values)):
            y = [self._x_values[i]]
            for k in keys:
                if k in self._results:
                    y.append(self._results[k][i])
                else:
                    y.append(None)
            yield y

    def __iter__(self):
        return self.zipped()

    def __len__(self):
        return len(self._x_values)

    def serialise_metadata(self):
        metadata = self.metadata.copy()
        for t in TIME_SETTINGS:
            if t in metadata and metadata[t] is not None:
                metadata[t] = metadata[t].isoformat()
        return metadata

    def serialise(self):
        metadata = self.serialise_metadata()
        return {
            'metadata': metadata,
            'version': FILEFORMAT_VERSION,
            'x_values': self._x_values,
            'results': self._results,
            'raw_values': self._raw_values,
            }

    @property
    def empty(self):
        return not self._x_values

    def dump(self, fp):
        data = self.dumps()
        if hasattr(data, "decode"):
            data = data.decode()

        return fp.write(data)

    def dumps(self):
        return json.dumps(self.serialise(), indent=JSON_INDENT, sort_keys=True)

    @property
    def dump_file(self):
        if hasattr(self, '_dump_file'):
            return self._dump_file
        return self._gen_filename()

    def _gen_filename(self):
        if self._filename is not None:
            return self._filename
        if 'TITLE' in self.metadata and self.metadata['TITLE']:
            return "%s-%s.%s%s" % (self.metadata['NAME'],
                                         self.metadata['TIME'].isoformat().replace(":", ""),
                                         re.sub("[^A-Za-z0-9]", "_", self.metadata['TITLE'])[:50],
                                         self.SUFFIX)
        else:
            return "%s-%s%s" % (self.metadata['NAME'], self.metadata['TIME'].isoformat().replace(":", ""), self.SUFFIX)

    def dump_dir(self, dirname):
        self._dump_file = os.path.join(dirname, self._gen_filename())
        try:
            if self._dump_file.endswith(".gz"):
                o = gzip_open
            elif self._dump_file.endswith(".bz2"):
                o = bz2_open
            else:
                o = open
            with o(self._dump_file, "wt") as fp:
                self.dump(fp)
        except IOError as e:
            sys.stderr.write("Unable to write results data file: %s\n" % e)
            self._dump_file = None

    @classmethod
    def unserialise(cls, obj, absolute=False, SUFFIX=SUFFIX):
        try:
            version = int(obj['version'])
        except (KeyError,ValueError):
            version = 1

        if version > FILEFORMAT_VERSION:
            raise RuntimeError("File format is version %d, but we only understand up to %d" % (version, FILEFORMAT_VERSION))
        if version < FILEFORMAT_VERSION:
            obj = cls.unserialise_compat(version, obj, absolute)
        metadata = dict(obj['metadata'])

        for t in TIME_SETTINGS:
            if t in metadata and metadata[t] is not None:
                metadata[t] = parse_date(metadata[t])
        rset = cls(SUFFIX=SUFFIX, **metadata)
        if absolute:
            t0 = metadata.get('T0', metadata.get('TIME'))
            x0 = timegm(t0.timetuple()) + t0.microsecond / 1000000.0
            rset.x_values = [x+x0 for x in obj['x_values']]
            rset._absolute = True
        else:
            rset.x_values = obj['x_values']
        for k,v in list(obj['results'].items()):
            rset.add_result(k,v)
        rset.raw_values = obj['raw_values']
        return rset

    @classmethod
    def unserialise_compat(cls, version, obj, absolute=False):
        if version == 1:
            obj['raw_values'] = {}
            if 'SERIES_META' in obj['metadata']:
                obj['raw_values'] = dict([(k,v['RAW_VALUES']) for k,v in
                                          obj['metadata']['SERIES_META'].items()
                                          if 'RAW_VALUES' in v])
            if not obj['raw_values']:
                # No raw values were stored in the old data set. Fake them by
                # using the interpolated values as 'raw'. This ensures there's
                # always some data available as raw values, to facilitate
                # relying on their presence in future code.

                t0 = parse_date(obj['metadata'].get('T0', obj['metadata'].get('TIME')))
                x0 = timegm(t0.timetuple()) + t0.microsecond / 1000000.0
                for name in obj['results'].keys():
                    obj['raw_values'][name] = [{'t': x0+x, 'val': r} for x,r in
                                               zip(obj['x_values'], obj['results'][name])]
                obj['metadata']['FAKE_RAW_VALUES'] = True

            if 'NETPERF_WRAPPER_VERSION' in obj['metadata']:
                obj['metadata']['FLENT_VERSION'] = obj['metadata']['NETPERF_WRAPPER_VERSION']
                del obj['metadata']['NETPERF_WRAPPER_VERSION']

        return obj

    @classmethod
    def load(cls, fp, absolute=False):
        if hasattr(fp, 'name'):
            name,ext = os.path.splitext(fp.name)
            if ext in ('.gz', '.bz2'):
                ext = os.path.splitext(name)[1]+ext
        else:
            ext = SUFFIX
        obj = cls.unserialise(json.load(fp), absolute, SUFFIX=ext)
        return obj

    @classmethod
    def load_file(cls, filename, absolute=False):
        try:
            if filename.endswith(".gz"):
                o = gzip_open
            elif filename.endswith(".bz2") or filename.endswith(".flnt"):
                o = bz2_open
            else:
                o = open
            fp = o(filename, 'rt')
            r = cls.load(fp, absolute)
            fp.close()
            return r
        except IOError:
            raise RuntimeError("Unable to read input file: '%s'" % filename)

    @classmethod
    def loads(cls, s):
        return cls.unserialise(json.loads(s))
