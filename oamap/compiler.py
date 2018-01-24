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

import pickle

import numpy

import oamap.generator
import oamap.proxy

try:
    import numba
    import llvmlite.llvmpy.core
except ImportError:
    pass
else:
    ################################################################ Baggage (tracing reference counts to reconstitute Python objects)

    class BaggageType(numba.types.Type):
        def __init__(self):
            super(BaggageType, self).__init__(name="OAMap-Baggage")

    baggagetype = BaggageType()

    @numba.extending.register_model(BaggageType)
    class BaggageModel(numba.datamodel.models.StructModel):
        def __init__(self, dmm, fe_type):
            members = [("arrays", numba.types.pyobject),
                       ("cache", numba.types.pyobject),
                       ("ptrs", numba.types.pyobject),
                       ("lens", numba.types.pyobject)]
            super(BaggageModel, self).__init__(dmm, fe_type, members)
            
    # def incref_baggage(context, builder, baggage_val):
    #     pyapi = context.get_python_api(builder)
    #     baggage = numba.cgutils.create_struct_proxy(baggagetype)(context, builder, value=baggage_val)
    #     pyapi.incref(baggage.arrays)
    #     pyapi.incref(baggage.cache)
    #     pyapi.incref(baggage.ptrs)
    #     pyapi.incref(baggage.lens)

    def unbox_baggage(context, builder, pyapi, generator_obj, arrays_obj, cache_obj):
        entercompiled_fcn = pyapi.object_getattr_string(generator_obj, "_entercompiled")
        results_obj = pyapi.call_function_objargs(entercompiled_fcn, (arrays_obj, cache_obj))
        with builder.if_then(numba.cgutils.is_not_null(builder, pyapi.err_occurred()), likely=False):
            builder.ret(llvmlite.llvmpy.core.Constant.null(pyapi.pyobj))

        baggage = numba.cgutils.create_struct_proxy(baggagetype)(context, builder)
        baggage.arrays = arrays_obj
        baggage.cache = cache_obj
        baggage.ptrs = pyapi.tuple_getitem(results_obj, 0)
        baggage.lens = pyapi.tuple_getitem(results_obj, 1)

        ptrs_obj = pyapi.tuple_getitem(results_obj, 2)
        lens_obj = pyapi.tuple_getitem(results_obj, 3)
        ptrs = pyapi.long_as_voidptr(ptrs_obj)
        lens = pyapi.long_as_voidptr(lens_obj)

        pyapi.decref(generator_obj)
        pyapi.decref(generator_obj)
        pyapi.decref(results_obj)

        return baggage._getvalue(), ptrs, lens

    def box_baggage(context, builder, pyapi, generator, baggage_val):
        generator_obj = pyapi.unserialize(pyapi.serialize_object(generator))
        new_fcn = pyapi.object_getattr_string(generator_obj, "_new")
        results_obj = pyapi.call_function_objargs(new_fcn, ())
        with builder.if_then(numba.cgutils.is_not_null(builder, pyapi.err_occurred()), likely=False):
            builder.ret(llvmlite.llvmpy.core.Constant.null(pyapi.pyobj))

        pyapi.decref(results_obj)

        baggage = numba.cgutils.create_struct_proxy(baggagetype)(context, builder, value=baggage_val)

        pyapi.decref(baggage.arrays)
        pyapi.decref(baggage.cache)

        return generator_obj, baggage.arrays, baggage.cache

    ################################################################ general routines for all types

    @numba.extending.typeof_impl.register(oamap.proxy.Proxy)
    def typeof_proxy(val, c):
        return typeof_generator(val._generator)

    def typeof_generator(generator, checkmasked=True):
        if checkmasked and isinstance(generator, oamap.generator.Masked):
            tpe = typeof_generator(generator, checkmasked=False)
            if isinstance(tpe, numba.types.Optional):
                return tpe
            else:
                return numba.types.optional(tpe)

        if isinstance(generator, oamap.generator.PrimitiveGenerator):
            if generator.dims == ():
                return numba.from_dtype(generator.dtype)
            else:
                raise NotImplementedError

        elif isinstance(generator, oamap.generator.ListGenerator):
            return ListProxyNumbaType(generator)

        elif isinstance(generator, oamap.generator.UnionGenerator):
            raise NotImplementedError

        elif isinstance(generator, oamap.generator.RecordGenerator):
            return RecordProxyNumbaType(generator)

        elif isinstance(generator, oamap.generator.TupleGenerator):
            return TupleProxyNumbaType(generator)

        elif isinstance(generator, oamap.generator.PointerGenerator):
            return typeof_generator(generator.target)

        elif isinstance(generator, oamap.generator.ExtendedGenerator):
            return typeof_generator(generator.generic)

        else:
            raise AssertionError("unrecognized generator type: {0} ({1})".format(generator.__class__, repr(generator)))

    def literal_int(value, itemsize):
        return llvmlite.llvmpy.core.Constant.int(llvmlite.llvmpy.core.Type.int(itemsize * 8), value)

    def literal_int64(value):
        return literal_int(value, 8)

    def literal_intp(value):
        return literal_int(value, numba.types.intp.bitwidth // 8)

    def cast_int(builder, value, itemsize):
        bitwidth = itemsize * 8
        if value.type.width < bitwidth:
            return builder.zext(value, llvmlite.llvmpy.core.Type.int(bitwidth))
        elif value.type.width > bitwidth:
            return builder.trunc(value, llvmlite.llvmpy.core.Type.int(bitwidth))
        else:
            return builder.bitcast(value, llvmlite.llvmpy.core.Type.int(bitwidth))

    def cast_int64(builder, value):
        return cast_int(builder, value, 8)

    def cast_intp(builder, value):
        return cast_int(builder, value, numba.types.intp.bitwidth // 8)

    def arrayitem(context, builder, idx, ptrs, lens, at, dtype):
        offset = builder.mul(idx, literal_int64(numba.types.intp.bitwidth // 8))

        ptrposition = builder.inttoptr(
            builder.add(builder.ptrtoint(ptrs, llvmlite.llvmpy.core.Type.int(numba.types.intp.bitwidth)), offset),
            llvmlite.llvmpy.core.Type.pointer(context.get_value_type(numba.types.intp)))

        lenposition = builder.inttoptr(
            builder.add(builder.ptrtoint(lens, llvmlite.llvmpy.core.Type.int(numba.types.intp.bitwidth)), offset),
            llvmlite.llvmpy.core.Type.pointer(context.get_value_type(numba.types.intp)))

        ptr = numba.targets.arrayobj.load_item(context, builder, numba.types.intp[:], ptrposition)
        len = numba.targets.arrayobj.load_item(context, builder, numba.types.intp[:], lenposition)

        raise_exception(context, builder, builder.icmp_unsigned(">=", at, len), RuntimeError("array index out of range"))

        finalptr = builder.inttoptr(
            builder.add(ptr, builder.mul(at, literal_int64(dtype.itemsize))),
            llvmlite.llvmpy.core.Type.pointer(context.get_value_type(numba.from_dtype(dtype))))

        return numba.targets.arrayobj.load_item(context, builder, numba.from_dtype(dtype)[:], finalptr)

    def raise_exception(context, builder, case, exception):
        with builder.if_then(case, likely=False):
            pyapi = context.get_python_api(builder)
            excptr = context.call_conv._get_excinfo_argument(builder.function)

            if excptr.name == "excinfo" and excptr.type == llvmlite.llvmpy.core.Type.pointer(llvmlite.llvmpy.core.Type.pointer(llvmlite.llvmpy.core.Type.struct([llvmlite.llvmpy.core.Type.pointer(llvmlite.llvmpy.core.Type.int(8)), llvmlite.llvmpy.core.Type.int(32)]))):
                exc = pyapi.serialize_object(exception)
                builder.store(exc, excptr)
                builder.ret(numba.targets.callconv.RETCODE_USEREXC)

            elif excptr.name == "py_args" and excptr.type == llvmlite.llvmpy.core.Type.pointer(llvmlite.llvmpy.core.Type.int(8)):
                exc = pyapi.unserialize(pyapi.serialize_object(exception))
                pyapi.raise_object(exc)
                builder.ret(llvmlite.llvmpy.core.Constant.null(context.get_value_type(numba.types.pyobject)))

            else:
                raise AssertionError("unrecognized exception calling convention: {0}".format(excptr))

    def generate_empty(context, builder, generator, baggage):
        typ = typeof_generator(generator, checkmasked=False)

        if isinstance(generator, oamap.generator.PrimitiveGenerator):
            if generator.dims == ():
                return llvmlite.llvmpy.core.Constant.null(context.get_value_type(numba.from_dtype(generator.dtype)))
            else:
                raise NotImplementedError

        elif isinstance(generator, oamap.generator.ListGenerator):
            listproxy = numba.cgutils.create_struct_proxy(typ)(context, builder)
            listproxy.baggage = baggage
            listproxy.ptrs = llvmlite.llvmpy.core.Constant.null(context.get_value_type(numba.types.voidptr))
            listproxy.lens = llvmlite.llvmpy.core.Constant.null(context.get_value_type(numba.types.voidptr))
            listproxy.whence = literal_int64(0)
            listproxy.stride = literal_int64(0)
            listproxy.length = literal_int64(0)
            return listproxy._getvalue()

        elif isinstance(generator, oamap.generator.UnionGenerator):
            raise NotImplementedError

        elif isinstance(generator, oamap.generator.RecordGenerator):
            recordproxy = numba.cgutils.create_struct_proxy(typ)(context, builder)
            recordproxy.baggage = baggage
            recordproxy.ptrs = llvmlite.llvmpy.core.Constant.null(context.get_value_type(numba.types.voidptr))
            recordproxy.lens = llvmlite.llvmpy.core.Constant.null(context.get_value_type(numba.types.voidptr))
            recordproxy.index = literal_int64(0)
            return recordproxy._getvalue()

        elif isinstance(generator, oamap.generator.TupleGenerator):
            tupleproxy = numba.cgutils.create_struct_proxy(typ)(context, builder)
            tupleproxy.baggage = baggage
            tupleproxy.ptrs = llvmlite.llvmpy.core.Constant.null(context.get_value_type(numba.types.voidptr))
            tupleproxy.lens = llvmlite.llvmpy.core.Constant.null(context.get_value_type(numba.types.voidptr))
            tupleproxy.index = literal_int64(0)
            return tupleproxy._getvalue()

        elif isinstance(generator, oamap.generator.PointerGenerator):
            return generate_empty(context, builder, generator.target, baggage)

        elif isinstance(generator, oamap.generator.ExtendedGenerator):
            return generate(context, builder, generator.generic, baggage, ptrs, lens, at)

        else:
            raise AssertionError("unrecognized generator type: {0} ({1})".format(generator.__class__, repr(generator)))

    def generate(context, builder, generator, baggage, ptrs, lens, at, checkmasked=True):
        generator._required = True

        if checkmasked and isinstance(generator, oamap.generator.Masked):
            maskidx = literal_int64(generator.maskidx)
            maskvalue = arrayitem(context, builder, maskidx, ptrs, lens, at, generator.maskdtype)

            comparison = builder.icmp_unsigned("==", maskvalue, literal_int(generator.maskedvalue, generator.maskdtype.itemsize))

            outoptval = context.make_helper(builder, typeof_generator(generator))

            if isinstance(generator, oamap.generator.PointerGenerator) and isinstance(generator.target, oamap.generator.Masked):
                with builder.if_else(comparison) as (is_not_valid, is_valid):
                    with is_valid:
                        nested = generate(context, builder, generator, baggage, ptrs, lens, cast_int64(builder, maskvalue), checkmasked=False)
                        wrapped = context.make_helper(builder, typeof_generator(generator), value=nested)
                        outoptval.valid = wrapped.valid
                        outoptval.data  = wrapped.data

                    with is_not_valid:
                        outoptval.valid = numba.cgutils.false_bit
                        outoptval.data = generate_empty(context, builder, generator, baggage)

            else:
                with builder.if_else(comparison) as (is_not_valid, is_valid):
                    with is_valid:
                        outoptval.valid = numba.cgutils.true_bit
                        outoptval.data = generate(context, builder, generator, baggage, ptrs, lens, cast_int64(builder, maskvalue), checkmasked=False)

                    with is_not_valid:
                        outoptval.valid = numba.cgutils.false_bit
                        outoptval.data = generate_empty(context, builder, generator, baggage)

            return outoptval._getvalue()

        typ = typeof_generator(generator, checkmasked=False)

        if isinstance(generator, oamap.generator.PrimitiveGenerator):
            if generator.dims == ():
                dataidx = literal_int64(generator.dataidx)
                return arrayitem(context, builder, dataidx, ptrs, lens, at, generator.dtype)
            else:
                raise NotImplementedError

        elif isinstance(generator, oamap.generator.ListGenerator):
            startsidx = literal_int64(generator.startsidx)
            stopsidx  = literal_int64(generator.stopsidx)
            start = cast_int64(builder, arrayitem(context, builder, startsidx, ptrs, lens, at, generator.posdtype))
            stop  = cast_int64(builder, arrayitem(context, builder, stopsidx,  ptrs, lens, at, generator.posdtype))

            listproxy = numba.cgutils.create_struct_proxy(typ)(context, builder)
            listproxy.baggage = baggage
            listproxy.ptrs = ptrs
            listproxy.lens = lens
            listproxy.whence = start
            listproxy.stride = literal_int64(1)
            listproxy.length = builder.sub(stop, start)
            return listproxy._getvalue()

        elif isinstance(generator, oamap.generator.UnionGenerator):
            raise NotImplementedError

        elif isinstance(generator, oamap.generator.RecordGenerator):
            recordproxy = numba.cgutils.create_struct_proxy(typ)(context, builder)
            recordproxy.baggage = baggage
            recordproxy.ptrs = ptrs
            recordproxy.lens = lens
            recordproxy.index = at
            return recordproxy._getvalue()

        elif isinstance(generator, oamap.generator.TupleGenerator):
            tupleproxy = numba.cgutils.create_struct_proxy(typ)(context, builder)
            tupleproxy.builder = baggage
            tupleproxy.ptrs = ptrs
            tupleproxy.lens = lens
            tupleproxy.index = at
            return tupleproxy._getvalue()

        elif isinstance(generator, oamap.generator.PointerGenerator):
            positionsidx = literal_int64(generator.positionsidx)
            index = cast_int64(builder, arrayitem(context, builder, positionsidx, ptrs, lens, at, generator.posdtype))
            return generate(context, builder, generator.target, baggage, ptrs, lens, index)

        elif isinstance(generator, oamap.generator.ExtendedGenerator):
            return generate(context, builder, generator.generic, baggage, ptrs, lens, at)

        else:
            raise AssertionError("unrecognized generator type: {0} ({1})".format(generator.__class__, repr(generator)))

    ################################################################ ListProxy

    class ListProxyNumbaType(numba.types.Type):
        def __init__(self, generator):
            self.generator = generator
            super(ListProxyNumbaType, self).__init__(name="OAMap-ListProxy-" + self.generator.id)

    @numba.extending.register_model(ListProxyNumbaType)
    class ListProxyModel(numba.datamodel.models.StructModel):
        def __init__(self, dmm, fe_type):
            members = [("baggage", baggagetype),
                       ("ptrs", numba.types.voidptr),
                       ("lens", numba.types.voidptr),
                       ("whence", numba.types.int64),
                       ("stride", numba.types.int64),
                       ("length", numba.types.int64)]
            super(ListProxyModel, self).__init__(dmm, fe_type, members)

    @numba.extending.type_callable(len)
    def listproxy_len_type(context):
        def typer(listproxy):
            if isinstance(listproxy, ListProxyNumbaType):
                return numba.types.int64   # verified len type
        return typer

    @numba.extending.lower_builtin(len, ListProxyNumbaType)
    def listproxy_len(context, builder, sig, args):
        listtpe, = sig.args
        listval, = args
        listproxy = numba.cgutils.create_struct_proxy(listtpe)(context, builder, value=listval)
        return listproxy.length

    @numba.typing.templates.infer
    class ListProxyGetItem(numba.typing.templates.AbstractTemplate):
        key = "getitem"
        def generic(self, args, kwds):
            tpe, idx = args
            if isinstance(tpe, ListProxyNumbaType):
                if isinstance(idx, numba.types.Integer):
                    return typeof_generator(tpe.generator.content)(tpe, idx)
                elif isinstance(idx, numba.types.SliceType):
                    return typeof_generator(tpe.generator)(tpe, idx)

    @numba.extending.lower_builtin("getitem", ListProxyNumbaType, numba.types.Integer)
    def listproxy_getitem(context, builder, sig, args):
        listtpe, indextpe = sig.args
        listval, indexval = args

        listproxy = numba.cgutils.create_struct_proxy(listtpe)(context, builder, value=listval)

        normindex_ptr = numba.cgutils.alloca_once(builder, llvmlite.llvmpy.core.Type.int(64))
        builder.store(indexval, normindex_ptr)
        with builder.if_then(builder.icmp_signed("<", indexval, literal_int64(0))):
            builder.store(builder.add(indexval, listproxy.length), normindex_ptr)
        normindex = builder.load(normindex_ptr)

        raise_exception(context,
                        builder,
                        builder.or_(builder.icmp_signed("<", normindex, literal_int64(0)),
                                    builder.icmp_signed(">=", normindex, listproxy.length)),
                        IndexError("index out of bounds"))

        at = builder.add(listproxy.whence, builder.mul(listproxy.stride, normindex))
        return generate(context, builder, listtpe.generator.content, listproxy.baggage, listproxy.ptrs, listproxy.lens, at)

    @numba.extending.lower_builtin("getitem", ListProxyNumbaType, numba.types.SliceType)
    def listproxy_getitem_slice(context, builder, sig, args):
        listtpe, indextpe = sig.args
        listval, indexval = args

        sliceproxy = context.make_helper(builder, indextpe, indexval)
        listproxy = numba.cgutils.create_struct_proxy(listtpe)(context, builder, value=listval)
        slicedlistproxy = numba.cgutils.create_struct_proxy(listtpe)(context, builder)

        numba.targets.slicing.guard_invalid_slice(context, builder, indextpe, sliceproxy)
        numba.targets.slicing.fix_slice(builder, sliceproxy, listproxy.length)

        slicedlistproxy.baggage = listproxy.baggage
        slicedlistproxy.ptrs = listproxy.ptrs
        slicedlistproxy.lens = listproxy.lens
        slicedlistproxy.whence = sliceproxy.start
        slicedlistproxy.stride = sliceproxy.step
        slicedlistproxy.length = numba.targets.slicing.get_slice_length(builder, sliceproxy)

        return slicedlistproxy._getvalue()

    @numba.extending.unbox(ListProxyNumbaType)
    def unbox_listproxy(typ, obj, c):
        generator_obj = c.pyapi.object_getattr_string(obj, "_generator")
        arrays_obj = c.pyapi.object_getattr_string(obj, "_arrays")
        cache_obj = c.pyapi.object_getattr_string(obj, "_cache")
        whence_obj = c.pyapi.object_getattr_string(obj, "_whence")
        stride_obj = c.pyapi.object_getattr_string(obj, "_stride")
        length_obj = c.pyapi.object_getattr_string(obj, "_length")

        listproxy = numba.cgutils.create_struct_proxy(typ)(c.context, c.builder)
        listproxy.baggage, listproxy.ptrs, listproxy.lens = unbox_baggage(c.context, c.builder, c.pyapi, generator_obj, arrays_obj, cache_obj)
        listproxy.whence = c.pyapi.long_as_longlong(whence_obj)
        listproxy.stride = c.pyapi.long_as_longlong(stride_obj)
        listproxy.length = c.pyapi.long_as_longlong(length_obj)

        c.pyapi.decref(whence_obj)
        c.pyapi.decref(stride_obj)
        c.pyapi.decref(length_obj)

        is_error = numba.cgutils.is_not_null(c.builder, c.pyapi.err_occurred())
        return numba.extending.NativeValue(listproxy._getvalue(), is_error=is_error)

    @numba.extending.box(ListProxyNumbaType)
    def box_listproxy(typ, val, c):
        listproxy = numba.cgutils.create_struct_proxy(typ)(c.context, c.builder, value=val)
        whence_obj = c.pyapi.long_from_longlong(listproxy.whence)
        stride_obj = c.pyapi.long_from_longlong(listproxy.stride)
        length_obj = c.pyapi.long_from_longlong(listproxy.length)

        listproxy_cls = c.pyapi.unserialize(c.pyapi.serialize_object(oamap.proxy.ListProxy))
        generator_obj, arrays_obj, cache_obj = box_baggage(c.context, c.builder, c.pyapi, typ.generator, listproxy.baggage)
        out = c.pyapi.call_function_objargs(listproxy_cls, (generator_obj, arrays_obj, cache_obj, whence_obj, stride_obj, length_obj))

        c.pyapi.decref(listproxy_cls)

        return out

    ################################################################ ListProxyIterator

    class ListProxyIteratorType(numba.types.common.SimpleIteratorType):
        def __init__(self, listproxytype):
            self.listproxy = listproxytype
            super(ListProxyIteratorType, self).__init__("iter({0})".format(listproxytype.name), typeof_generator(listproxytype.generator.content))

    @numba.datamodel.registry.register_default(ListProxyIteratorType)
    class ListProxyIteratorModel(numba.datamodel.models.StructModel):
        def __init__(self, dmm, fe_type):
            members = [("index", numba.types.EphemeralPointer(numba.types.int64)),
                       ("listproxy", fe_type.listproxy)]
            super(ListProxyIteratorModel, self).__init__(dmm, fe_type, members)

    @numba.typing.templates.infer
    class ListProxy_getiter(numba.typing.templates.AbstractTemplate):
        key = "getiter"
        def generic(self, args, kwds):
            objtyp, = args
            if isinstance(objtyp, ListProxyNumbaType):
                return numba.typing.templates.signature(ListProxyIteratorType(objtyp), objtyp)

    @numba.extending.lower_builtin("getiter", ListProxyNumbaType)
    def listproxy_getiter(context, builder, sig, args):
        listtpe, = sig.args
        listval, = args

        iterobj = context.make_helper(builder, sig.return_type)
        iterobj.index = numba.cgutils.alloca_once_value(builder, literal_int64(0))
        iterobj.listproxy = listval

        if context.enable_nrt:
            context.nrt.incref(builder, listtpe, listval)

        return numba.targets.imputils.impl_ret_new_ref(context, builder, sig.return_type, iterobj._getvalue())

    @numba.extending.lower_builtin("iternext", ListProxyIteratorType)
    @numba.targets.imputils.iternext_impl
    def listproxy_iternext(context, builder, sig, args, result):
        itertpe, = sig.args
        iterval, = args
        iterproxy = context.make_helper(builder, itertpe, value=iterval)
        listproxy = numba.cgutils.create_struct_proxy(itertpe.listproxy)(context, builder, value=iterproxy.listproxy)

        index = builder.load(iterproxy.index)
        is_valid = builder.icmp_signed("<", index, listproxy.length)
        result.set_valid(is_valid)

        with builder.if_then(is_valid, likely=True):
            at = builder.add(listproxy.whence, builder.mul(listproxy.stride, index))
            result.yield_(generate(context, builder, itertpe.listproxy.generator.content, listproxy.baggage, listproxy.ptrs, listproxy.lens, at))
            nextindex = numba.cgutils.increment_index(builder, index)
            builder.store(nextindex, iterproxy.index)

    ################################################################ UnionProxy

    # class UnionProxyNumbaType(numba.types.Type):
    #     def __init__(self, generator):
    #         super(RecordProxyNumbaType, self).__init__(name="OAMap-UnionProxy-" + self.generator.id)

    # @numba.extending.register_model(UnionProxyNumbaType)
    # class UnionProxyModel(numba.datamodel.models.StructModel):
    #     def __init__(self, dmm, fe_type):
    #         members = [("baggage", baggagetype),
    #                    ("ptrs", numba.types.voidptr),
    #                    ("lens", numba.types.voidptr),
    #                    ("index", 



    ################################################################ RecordProxy

    class RecordProxyNumbaType(numba.types.Type):
        def __init__(self, generator):
            self.generator = generator
            super(RecordProxyNumbaType, self).__init__(name="OAMap-RecordProxy-" + self.generator.id)

    @numba.extending.register_model(RecordProxyNumbaType)
    class RecordProxyModel(numba.datamodel.models.StructModel):
        def __init__(self, dmm, fe_type):
            members = [("baggage", baggagetype),
                       ("ptrs", numba.types.voidptr),
                       ("lens", numba.types.voidptr),
                       ("index", numba.types.int64)]
            super(RecordProxyModel, self).__init__(dmm, fe_type, members)

    @numba.extending.infer_getattr
    class StructAttribute(numba.typing.templates.AttributeTemplate):
        key = RecordProxyNumbaType
        def generic_resolve(self, typ, attr):
            fieldgenerator = typ.generator.fields.get(attr, None)
            if fieldgenerator is not None:
                return typeof_generator(fieldgenerator)
            else:
                raise AttributeError("{0} object has no attribute {1}".format(repr("Record" if typ.generator.name is None else typ.generator.name), repr(attr)))

    @numba.extending.lower_getattr_generic(RecordProxyNumbaType)
    def recordproxy_getattr(context, builder, typ, val, attr):
        recordproxy = numba.cgutils.create_struct_proxy(typ)(context, builder, value=val)
        return generate(context, builder, typ.generator.fields[attr], recordproxy.baggage, recordproxy.ptrs, recordproxy.lens, recordproxy.index)

    @numba.extending.unbox(RecordProxyNumbaType)
    def unbox_recordproxy(typ, obj, c):
        generator_obj = c.pyapi.object_getattr_string(obj, "_generator")
        arrays_obj = c.pyapi.object_getattr_string(obj, "_arrays")
        cache_obj = c.pyapi.object_getattr_string(obj, "_cache")
        index_obj = c.pyapi.object_getattr_string(obj, "_index")

        recordproxy = numba.cgutils.create_struct_proxy(typ)(c.context, c.builder)
        recordproxy.baggage, recordproxy.ptrs, recordproxy.lens = unbox_baggage(c.context, c.builder, c.pyapi, generator_obj, arrays_obj, cache_obj)
        recordproxy.index = c.pyapi.long_as_longlong(index_obj)

        c.pyapi.decref(index_obj)

        is_error = numba.cgutils.is_not_null(c.builder, c.pyapi.err_occurred())
        return numba.extending.NativeValue(recordproxy._getvalue(), is_error=is_error)

    @numba.extending.box(RecordProxyNumbaType)
    def box_recordproxy(typ, val, c):
        recordproxy = numba.cgutils.create_struct_proxy(typ)(c.context, c.builder, value=val)
        index_obj = c.pyapi.long_from_longlong(recordproxy.index)

        recordproxy_cls = c.pyapi.unserialize(c.pyapi.serialize_object(oamap.proxy.RecordProxy))
        generator_obj, arrays_obj, cache_obj = box_baggage(c.context, c.builder, c.pyapi, typ.generator, recordproxy.baggage)
        out = c.pyapi.call_function_objargs(recordproxy_cls, (generator_obj, arrays_obj, cache_obj, index_obj))

        c.pyapi.decref(recordproxy_cls)

        return out

    ################################################################ TupleProxy

    class TupleProxyNumbaType(numba.types.Type):
        def __init__(self, generator):
            self.generator = generator
            super(TupleProxyNumbaType, self).__init__(name="OAMap-TupleProxy-" + self.generator.id)

    @numba.extending.register_model(TupleProxyNumbaType)
    class TupleProxyModel(numba.datamodel.models.StructModel):
        def __init__(self, dmm, fe_type):
            members = [("baggage", baggagetype),
                       ("ptrs", numba.types.voidptr),
                       ("lens", numba.types.voidptr),
                       ("index", numba.types.int64)]
            super(TupleProxyModel, self).__init__(dmm, fe_type, members)

    @numba.extending.type_callable(len)
    def tupleproxy_len_type(context):
        def typer(tupleproxy):
            if isinstance(tupleproxy, TupleProxyNumbaType):
                return numba.types.int64   # verified len type
        return typer

    @numba.extending.lower_builtin(len, TupleProxyNumbaType)
    def tupleproxy_len(context, builder, sig, args):
        listtpe, = sig.args
        return literal_int64(len(listtpe.generator.types))

    @numba.typing.templates.infer
    class TupleProxyGetItem(numba.typing.templates.AbstractTemplate):
        key = "static_getitem"
        def generic(self, args, kwds):
            tpe, idx = args
            if isinstance(tpe, TupleProxyNumbaType):
                if isinstance(idx, int):
                    if idx < 0:
                        normindex = idx + len(tpe.generator.types)
                    else:
                        normindex = idx
                    if 0 <= normindex < len(tpe.generator.types):
                        return typeof_generator(tpe.generator.types[normindex])

    @numba.extending.lower_builtin("static_getitem", TupleProxyNumbaType, numba.types.Const)
    def tupleproxy_static_getitem(context, builder, sig, args):
        tupletpe, _ = sig.args
        tupleval, idx = args
        if isinstance(idx, int):
            if idx < 0:
                normindex = idx + len(tupletpe.generator.types)
            else:
                normindex = idx
            tupleproxy = numba.cgutils.create_struct_proxy(tupletpe)(context, builder, value=tupleval)
            return generate(context, builder, tupletpe.generator.types[normindex], tupleproxy.baggage, tupleproxy.ptrs, tupleproxy.lens, tupleproxy.index)
            
    @numba.extending.unbox(TupleProxyNumbaType)
    def unbox_tupleproxy(typ, obj, c):
        generator_obj = c.pyapi.object_getattr_string(obj, "_generator")
        arrays_obj = c.pyapi.object_getattr_string(obj, "_arrays")
        cache_obj = c.pyapi.object_getattr_string(obj, "_cache")
        index_obj = c.pyapi.object_getattr_string(obj, "_index")

        tupleproxy = numba.cgutils.create_struct_proxy(typ)(c.context, c.builder)
        tupleproxy.baggage, tupleproxy.ptrs, tupleproxy.lens = unbox_baggage(c.context, c.builder, c.pyapi, generator_obj, arrays_obj, cache_obj)
        tupleproxy.index = c.pyapi.long_as_longlong(index_obj)

        c.pyapi.decref(index_obj)

        is_error = numba.cgutils.is_not_null(c.builder, c.pyapi.err_occurred())
        return numba.extending.NativeValue(tupleproxy._getvalue(), is_error=is_error)

    @numba.extending.box(TupleProxyNumbaType)
    def box_tupleproxy(typ, val, c):
        tupleproxy = numba.cgutils.create_struct_proxy(typ)(c.context, c.builder, value=val)
        index_obj = c.pyapi.long_from_longlong(tupleproxy.index)

        tupleproxy_cls = c.pyapi.unserialize(c.pyapi.serialize_object(oamap.proxy.TupleProxy))
        generator_obj, arrays_obj, cache_obj = box_baggage(c.context, c.builder, c.pyapi, typ.generator, tupleproxy.baggage)
        out = c.pyapi.call_function_objargs(tupleproxy_cls, (generator_obj, arrays_obj, cache_obj, index_obj))

        c.pyapi.decref(tupleproxy_cls)

        return out

    ################################################################ PartitionedListProxy

    ################################################################ IndexedPartitionedListProxy

