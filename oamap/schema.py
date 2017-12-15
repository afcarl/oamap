#!/usr/bin/env python

# Copyright (c) 2017, DIANA-HEP
# All rights reserved.
# 
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
# 
# * Redistributions of source code must retain the above copyright notice, this
#   list of conditions and the following disclaimer.
# 
# * Redistributions in binary form must reproduce the above copyright notice,
#   this list of conditions and the following disclaimer in the documentation
#   and/or other materials provided with the distribution.
# 
# * Neither the name of the copyright holder nor the names of its
#   contributors may be used to endorse or promote products derived from
#   this software without specific prior written permission.
# 
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
# FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
# DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
# SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
# CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
# OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

import re
import sys
import numbers
from types import MethodType
try:
    from collections import OrderedDict
except ImportError:
    # simple OrderedDict implementation for Python 2.6
    class OrderedDict(dict):
        def __init__(self, items=(), **kwds):
            items = list(items)
            self._order = [k for k, v in items] + [k for k, v in kwds.items()]
            super(OrderedDict, self).__init__(items)
        def keys(self):
            return self._order
        def values(self):
            return [self[k] for k in self._order]
        def items(self):
            return [(k, self[k]) for k in self._order]
        def __setitem__(self, name, order):
            if name not in self._order:
                self._order.append(name)
            super(OrderedDict, self).__setitem__(name, value)
        def __delitem__(self, name):
            if name in self._order:
                self._order.remove(name)
            super(OrderedDict, self).__delitem__(name)
        def __repr__(self):
            return "OrderedDict([{0}])".format(", ".join("({0}, {1})".format(repr(k), repr(v)) for k, v in self.items()))

import numpy

from oamap.proxy import *

if sys.version_info[0] > 2:
    basestring = str

# The "PLURTP" type system: Primitives, Lists, Unions, Records, Tuples, and Pointers

class Schema(object):
    def __init__(self, *args, **kwds):
        raise TypeError("Kind cannot be instantiated directly")

    @property
    def nullable(self):
        return self._nullable

    @nullable.setter
    def nullable(self, value):
        if value is not True and value is not False:
            raise TypeError("nullable must be True or False, not {0}".format(repr(value)))
        self._nullable = value

    @property
    def mask(self):
        return self._mask

    @mask.setter
    def mask(self, value):
        if not (value is None or isinstance(value, basestring)):
            raise TypeError("mask must be None or an array name (string), not {0}".format(repr(value)))
        self._mask = value

    @property
    def name(self):
        return self._name

    @name.setter
    def name(self, value):
        if not (value is None or isinstance(value, basestring)):
            raise TypeError("name must be None or a string, not {0}".format(repr(value)))
        self._name = value

    def _labels(self):
        labels = []
        self._collectlabels(set(), labels)
        return labels
        
    def _label(self, labels):
        for index, label in enumerate(labels):
            if label is self:
                return "#{0}".format(index)
        return None

    def _resolvetargets(self, out, memo):
        for result in memo.values():
            if isinstance(result, (PointerType, MaskedPointerType)):
                # only assign pointer targets after all other types have been resolved
                target, prefix, delimiter = result.target
                if id(target) in memo:
                    # the target points elsewhere in the type tree: link to that
                    result.target = memo[id(target)]
                else:
                    # the target is not in the type tree: resolve it (including cases that might contain a type already seen; they're considered to be different types at different positions)
                    result.target = target(prefix=(prefix + delimiter + "P"), delimiter=delimiter)
        return out

################################################################ Primitives can be any Numpy type

class Primitive(Schema):
    def __init__(self, dtype, dims=(), nullable=False, data=None, mask=None, name=None):
        self.dtype = dtype
        self.dims = dims
        self.nullable = nullable
        self.data = data
        self.mask = mask
        self.name = name

    @property
    def dtype(self):
        return self._dtype

    @dtype.setter
    def dtype(self, value):
        if not isinstance(value, numpy.dtype):
            value = numpy.dtype(value)
        self._dtype = value

    @property
    def dims(self):
        return self._dims

    @dims.setter
    def dims(self, value):
        if not isinstance(value, tuple) or not all(isinstance(x, numbers.Integral) and x >= 0 for x in value):
            raise TypeError("dims must be a tuple of non-negative integers, not {0}".format(repr(value)))
        self._dims = value

    @property
    def data(self):
        return self._data

    @data.setter
    def data(self, value):
        if not (value is None or isinstance(value, basestring)):
            raise TypeError("data must be None or an array name (string), not {0}".format(repr(value)))
        self._data = value

    def __repr__(self, labels=None, shown=None):
        if labels is None:
            labels = self._labels()
            shown = set()
        label = self._label(labels)

        if label is None or id(self) not in shown:
            shown.add(id(self))

            args = [repr(self._dtype)]
            if self._dims != ():
                args.append("dims=" + repr(self._dims))
            if self._nullable is not False:
                args.append("nullable=" + repr(self._nullable))
            if self._data is not None:
                args.append("data=" + repr(self._data))
            if self._mask is not None:
                args.append("mask=" + repr(self._mask))

            if label is None:
                return "Primitive(" + ", ".join(args) + ")"
            else:
                return label + ": Primitive(" + ", ".join(args) + ")"

        else:
            return label

    def _collectlabels(self, collection, labels):
        if id(self) not in collection:
            collection.add(id(self))
        else:
            labels.append(self)

    def __call__(self, prefix="object", delimiter="-"):
        memo = {}
        return self._resolvetargets(self._totype(prefix, delimiter, memo), memo)

    def _totype(self, prefix, delimiter, memo):
        if id(self) in memo:
            raise TypeError("types may not be defined in terms of themselves:\n\n    {0}".format(repr(self)))
        memo[id(self)] = None

        if self._data is None:
            data = prefix
        else:
            data = self._data

        if not self._nullable:
            out = type("PrimitiveType", (PrimitiveType,), {"data": data, "name": self._name})

        else:
            if self._mask is None:
                mask = prefix + delimiter + "M"
            else:
                mask = self._mask

            out = type("MaskedPrimitiveType", (MaskedPrimitiveType,), {"data": data, "mask": mask, "name": self._name})

        memo[id(self)] = out
        return out

################################################################ Lists may have arbitrary length

class List(Schema):
    def __init__(self, content, nullable=False, starts=None, stops=None, mask=None, name=None):
        self.content = content
        self.nullable = nullable
        self.starts = starts
        self.stops = stops
        self.mask = mask
        self.name = name

    @property
    def content(self):
        return self._content

    @content.setter
    def content(self, value):
        if not isinstance(value, Schema):
            raise TypeError("content must be a Schema, not {0}".format(repr(value)))
        self._content = value

    @property
    def starts(self):
        return self._starts

    @starts.setter
    def starts(self, value):
        if not (value is None or isinstance(value, basestring)):
            raise TypeError("starts must be None or an array name (string), not {0}".format(repr(value)))
        self._starts = value

    @property
    def stops(self):
        return self._stops

    @stops.setter
    def stops(self, value):
        if not (value is None or isinstance(value, basestring)):
            raise TypeError("stops must be None or an array name (string), not {0}".format(repr(value)))
        self._stops = value

    def __repr__(self, labels=None, shown=None):
        if labels is None:
            labels = self._labels()
            shown = set()
        label = self._label(labels)

        if label is None or id(self) not in shown:
            shown.add(id(self))

            args = [self._content.__repr__(labels, shown)]
            if self._nullable is not False:
                args.append("nullable=" + repr(self._nullable))
            if self._starts is not None:
                args.append("starts=" + repr(self._starts))
            if self._stops is not None:
                args.append("stops=" + repr(self._stops))
            if self._mask is not None:
                args.append("mask=" + repr(self._mask))

            if label is None:
                return "List(" + ", ".join(args) + ")"
            else:
                return label + ": List(" + ", ".join(args) + ")"

        else:
            return label

    def _collectlabels(self, collection, labels):
        if id(self) not in collection:
            collection.add(id(self))
            self._content._collectlabels(collection, labels)
        else:
            labels.append(self)

    def __call__(self, prefix="object", delimiter="-"):
        memo = {}
        return self._resolvetargets(self._totype(prefix, delimiter, memo), memo)

    def _totype(self, prefix, delimiter, memo):
        if id(self) in memo:
            raise TypeError("types may not be defined in terms of themselves:\n\n    {0}".format(repr(self)))
        memo[id(self)] = None

        if self._starts is None:
            starts = prefix + delimiter + "B"
        else:
            starts = self._starts

        if self._stops is None:
            stops = prefix + delimiter + "E"
        else:
            stops = self._stops

        content = self._content._totype(prefix + delimiter + "L", delimiter, memo)
        if self._name is None:
            proxytype = type("AnonymousList", (AnonymousListProxy,), {"_content": content})
        else:
            proxytype = type(self._name, (ListProxy,), {"_content": content})

        if not self._nullable:
            out = type("ListType", (ListType,), {"proxytype": proxytype, "starts": starts, "stops": stops, "name": self._name})

        else:
            if self._mask is None:
                mask = prefix + delimiter + "M"
            else:
                mask = self._mask

            out = type("MaskedListType", (MaskedListType,), {"proxytype": proxytype, "starts": starts, "stops": stops, "mask": mask, "name": self._name})

        memo[id(self)] = out
        return out

################################################################ Unions may be one of several types

class Union(Schema):
    def __init__(self, possibilities, nullable=False, tags=None, offsets=None, mask=None, name=None):
        self.possibilities = possibilities
        self.nullable = nullable
        self.tags = tags
        self.offsets = offsets
        self.mask = mask
        self.name = name

    @property
    def possibilities(self):
        return tuple(self._possibilities)

    @possibilities.setter
    def possibilities(self, value):
        self._extend(value, [])

    def _extend(self, possibilities, start):
        trial = []
        try:
            for i, x in enumerate(value):
                assert isinstance(x, Schema), "possibilities must be an iterable of Schemas; item at {0} is {1}".format(i, repr(x))
                trial.append(x)
        except TypeError:
            raise TypeError("possibilities must be an iterable of Schemas, not {0}".format(repr(value)))
        except AssertionError as err:
            raise TypeError(err.message)
        self._possibilities = start + trial

    def append(self, possibility):
        if not isinstance(possibility, Schema):
            raise TypeError("possibilities must be Schemas, not {0}".format(repr(possibility)))
        self._possibilities.append(possibility)

    def insert(self, index, possibility):
        if not isinstance(possibility, Schema):
            raise TypeError("possibilities must be Schemas, not {0}".format(repr(possibility)))
        self._possibilities.insert(index, possibility)

    def extend(self, possibilities):
        self._extend(possibilities, self._possibilities)

    def __getitem__(self, index):
        return self._possibilities[index]

    def __setitem__(self, index, value):
        if not isinstance(value, Schema):
            raise TypeError("possibilities must be Schemas, not {0}".format(repr(value)))
        self._possibilities[index] = value

    def __repr__(self, labels=None, shown=None):
        if labels is None:
            labels = self._labels()
            shown = set()
        label = self._label(labels)

        if label is None or id(self) not in shown:
            shown.add(id(self))

            args = ["[" + ", ".join(x.__repr__(labels, shown) for x in self._possibilities) + "]"]
            if self._nullable is not False:
                args.append("nullable=" + repr(self._nullable))
            if self._tags is not None:
                args.append("tags=" + repr(self._tags))
            if self._offsets is not None:
                args.append("offsets=" + repr(self._offsets))
            if self._mask is not None:
                args.append("mask=" + repr(self._mask))

            if label is None:
                return "Union(" + ", ".join(args) + ")"
            else:
                return label + ": Union(" + ", ".join(args) + ")"

        else:
            return label

    def _collectlabels(self, collection, labels):
        if id(self) not in collection:
            collection.add(id(self))
            for possibility in self._possibilities:
                possibility._collectlabels(collection, labels)
        else:
            labels.append(self)

    def __call__(self, prefix="object", delimiter="-"):
        memo = {}
        return self._resolvetargets(self._totype(prefix, delimiter, memo), memo)

    def _totype(self, prefix, delimiter, memo):
        if id(self) in memo:
            raise TypeError("types may not be defined in terms of themselves:\n\n    {0}".format(repr(self)))
        memo[id(self)] = None

        if self._tags is None:
            tags = prefix + delimiter + "G"
        else:
            tags = self._tags

        if self._offsets is None:
            offsets = prefix + delimiter + "O"
        else:
            offsets = self._offsets

        possibilities = [x._totype(prefix + delimiter + "U" + repr(i), delimiter, memo) for i, x in enumerate(self._possibilities)]

        if not self._nullable:
            out = type("UnionType", (UnionType,), {"possibilities": possibilities, "tags": tags, "offsets": offsets, "name": self._name})

        else:
            if self._mask is None:
                mask = prefix + delimiter + "M"
            else:
                mask = self._mask

            out = type("MaskedUnionType", (MaskedUnionType,), {"possibilities": possibilities, "tags": tags, "offsets": offsets, "mask": mask, "name": self._name})

        memo[id(self)] = out
        return out

################################################################ Records contain fields of known types

class Record(Schema):
    def __init__(self, fields, nullable=False, mask=None, name=None):
        self.fields = fields
        self.nullable = nullable
        self.mask = mask
        self.name = name

    @property
    def fields(self):
        return dict(self._fields)

    @fields.setter
    def fields(self, value):
        self._extend(value, [])

    _identifier = re.compile("[a-zA-Z_][a-zA-Z_0-9]*")

    def _extend(self, fields, start):
        trial = []
        try:
            for n, x in fields.items():
                assert isinstance(n, basestring) and self._identifier.match(n) is not None, "fields must be a dict from identifier strings to Schemas; the key {0} is not an identifier (/{1}/)".format(repr(n), self._identifier.pattern)
                assert isinstance(x, Schema), "fields must be a dict from identifier strings to Schemas; the value at key {0} is {1}".format(repr(n), repr(x))
                trial.append((n, x))
        except AttributeError:
            raise TypeError("fields must be a dict from strings to Schemas; {0} is not a dict".format(repr(fields)))
        except AssertionError as err:
            raise TypeError(err.message)
        self._fields = OrderedDict(start + trial)

    def __getitem__(self, index):
        return self._fields[index]

    def __setitem__(self, index, value):
        if not isinstance(value, Schema):
            raise TypeError("field values must be Schemas, not {0}".format(repr(value)))
        self._fields[index] = value

    def __repr__(self, labels=None, shown=None):
        if labels is None:
            labels = self._labels()
            shown = set()
        label = self._label(labels)

        if label is None or id(self) not in shown:
            shown.add(id(self))

            args = ["{" + ", ".join("{0}: {1}".format(repr(n), repr(x)) for n, x in self._fields.items()) + "}"]
            if self._nullable is not False:
                args.append("nullable=" + repr(self._nullable))
            if self._mask is not None:
                args.append("mask=" + repr(self._mask))

            if label is None:
                return "Record(" + ", ".join(args) + ")"
            else:
                return label + ": Record(" + ", ".join(args) + ")"

        else:
            return label

    def _collectlabels(self, collection, labels):
        if id(self) not in collection:
            collection.add(id(self))
            for field in self._fields.values():
                field._collectlabels(collection, labels)
        else:
            labels.append(self)

    def __call__(self, prefix="object", delimiter="-"):
        memo = {}
        return self._resolvetargets(self._totype(prefix, delimiter, memo), memo)

    def _totype(self, prefix, delimiter, memo):
        if id(self) in memo:
            raise TypeError("types may not be defined in terms of themselves:\n\n    {0}".format(repr(self)))
        memo[id(self)] = None

        def wrap_for_python_scope(t):
            return lambda self: t(self._arrays, self._index)

        fields = tuple(sorted(self._fields))
        properties = dict((n, property(wrap_for_python_scope(self._fields[n]._totype(prefix + delimiter + "F" + n, delimiter, memo)))) for n in fields)
        properties["_fields"] = fields
        proxytype = type("AnonymousRecord" if self._name is None else self._name, (RecordProxy,), properties)

        if not self._nullable:
            out = type("RecordType", (RecordType,), {"proxytype": proxytype, "name": self._name})
        else:
            if self._mask is None:
                mask = prefix + delimiter + "M"
            else:
                mask = self._mask

            out = type("MaskedRecordType", (MaskedRecordType,), {"proxytype": proxytype, "mask": mask, "name": self._name})

        memo[id(self)] = out
        return out

################################################################ Tuples are like records but with an order instead of field names

class Tuple(Schema):
    def __init__(self, types, nullable=False, mask=None, name=None):
        self.types = types
        self.nullable = nullable
        self.mask = mask
        self.name = name

    @property
    def types(self):
        return tuple(self._types)

    @types.setter
    def types(self, value):
        self._extend(value, [])

    def _extend(self, types, start):
        trial = []
        try:
            for i, x in enumerate(value):
                assert isinstance(x, Schema), "types must be an iterable of Schemas; item at {0} is {1}".format(i, repr(x))
                trial.append(x)
        except TypeError:
            raise TypeError("types must be an iterable of Schemas, not {0}".format(repr(value)))
        except AssertionError as err:
            raise TypeError(err.message)
        self._types = start + trial

    def append(self, item):
        if not isinstance(item, Schema):
            raise TypeError("types must be Schemas, not {0}".format(repr(item)))
        self._types.append(item)

    def insert(self, index, item):
        if not isinstance(item, Schema):
            raise TypeError("types must be Schemas, not {0}".format(repr(item)))
        self._types.insert(index, item)

    def extend(self, types):
        self._extend(types, self._types)

    def __getitem__(self, index):
        return self._types[index]

    def __setitem__(self, index, value):
        if not isinstance(item, Schema):
            raise TypeError("types must be Schemas, not {0}".format(repr(value)))
        self._types[index] = value

    def __repr__(self, labels=None, shown=None):
        if labels is None:
            labels = self._labels()
            shown = set()
        label = self._label(labels)

        if label is None or id(self) not in shown:
            shown.add(id(self))

            args = ["[" + ", ".join(x.__repr__(labels, shown) for x in self._types) + "]"]
            if self._nullable is not False:
                args.append("nullable=" + repr(self._nullable))
            if self._mask is not None:
                args.append("mask=" + repr(self._mask))

            if label is None:
                return "Tuple(" + ", ".join(args) + ")"
            else:
                return label + "Tuple(" + ", ".join(args) + ")"

    def _collectlabels(self, collection, labels):
        if id(self) not in collection:
            collection.add(id(self))
            for item in self._types:
                item._collectlabels(collection, labels)
        else:
            labels.append(self)

    def __call__(self, prefix="object", delimiter="-"):
        memo = {}
        return self._resolvetargets(self._totype(prefix, delimiter, memo), memo)

    def _totype(self, prefix, delimiter, memo):
        if id(self) in memo:
            raise TypeError("types may not be defined in terms of themselves:\n\n    {0}".format(repr(self)))
        memo[id(self)] = None

        types = tuple(x._totype(prefix + delimiter + "T" + repr(i), delimiter, memo) for i, x in enumerate(self._types))

        if self._name is None:
            proxytype = type("AnonymousTuple", (AnonymousTupleProxy,), {"_types": types})
        else:
            proxytype = type(self._name, (TupleProxy,), {"_types": types})

        if not self._nullable:
            out = type("TupleType", (TupleType,), {"proxytype": proxytype, "name": self._name})

        else:
            if self._mask is None:
                mask = prefix + delimiter + "M"
            else:
                mask = self._mask

            out = type("MaskedTupleType", (MaskedTupleType,), {"proxytype": proxytype, "mask": mask, "name": self._name})

        memo[id(self)] = out
        return out

################################################################ Pointers redirect to Lists with absolute addresses

class Pointer(Schema):
    def __init__(self, target, nullable=False, indexes=None, mask=None, name=None):
        self.target = target
        self.nullable = nullable
        self.indexes = indexes
        self.mask = mask
        self.name = name

    @property
    def target(self):
        return self._target

    @target.setter
    def target(self, value):
        if not isinstance(value, Schema):
            raise TypeError("target must be a Schema, not {0}".format(repr(value)))
        self._target = target

    @property
    def indexes(self):
        return self._indexes

    @indexes.setter
    def indexes(self, value):
        if not (value is None or isinstance(value, basestring)):
            raise TypeError("indexes must be None or an array name (string), not {0}".format(repr(value)))
        self._indexes = value

    def __repr__(self, labels=None, shown=None):
        if labels is None:
            labels = self._labels()
            shown = set()
        label = self._label(labels)

        if label is None or id(self) not in shown:
            shown.add(id(self))

            args = [self._target.__repr__(labels, shown)]
            if self._nullable is not False:
                args.append("nullable=" + repr(self._nullable))
            if self._indexes is not None:
                args.append("indexes=" + repr(self._indexes))
            if self._mask is not None:
                args.append("mask=" + repr(self._mask))

            if label is None:
                return "Pointer(" + ", ".join(args) + ")"
            else:
                return label + "Pointer(" + ", ".join(args) + ")"

        else:
            return label

    def _collectlabels(self, collection, labels):
        if id(self) not in collection:
            collection.add(id(self))
            self._target._collectlabels(collection, labels)
        else:
            labels.append(self)

    def __call__(self, prefix="object", delimiter="-"):
        memo = {}
        return self._resolvetargets(self._totype(prefix, delimiter, memo), memo)

    def _totype(self, prefix, delimiter, memo):
        if self._indexes is None:
            indexes = prefix + delimiter + "I"
        else:
            indexes = self._indexes

        # don't recurse over _target until _resolvetargets; for now put a 3-tuple in its place, which will be used to construct the target type

        if not self._nullable:
            out = type("PointerType", (PointerType,), {"target": (self._target, prefix, delimiter), "indexes": indexes, "name": self._name})

        else:
            if self._mask is None:
                mask = prefix + delimiter + "M"
            else:
                mask = self._mask

            out = type("MaskedPointerType", (MaskedPointerType,), {"target": (self._target, prefix, delimiter), "indexes": indexes, "mask": mask, "name": self._name})

        return out