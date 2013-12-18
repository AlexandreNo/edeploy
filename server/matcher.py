#
# Copyright (C) 2013 eNovance SAS <licensing@enovance.com>
#
# Author: Frederic Lepied <frederic.lepied@enovance.com>
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

'''Functions to match according to a requirement specification.'''

import re
import sys
try:
    import ipaddr
    _HAS_IPADDR = True
except ImportError:
    _HAS_IPADDR = False

_FUNC_REGEXP = re.compile(r'^(.*)\((.*)\)')


def _adder(array, index, value):
    'Auxiliary function to add a value to an array.'
    array[index] = value


def _appender(array, index, value):
    'Auxiliary function to append a value to an array.'
    try:
        array[index].append(value)
    except KeyError:
        array[index] = [value, ]


def _gt(left, right):
    'Helper for match_spec.'
    return int(left) > int(right)


def _ge(left, right):
    'Helper for match_spec.'
    return int(left) >= int(right)


def _lt(left, right):
    'Helper for match_spec.'
    return int(left) < int(right)


def _le(left, right):
    'Helper for match_spec.'
    return int(left) <= int(right)


def _network(left, right):
    'Helper for match_spec.'
    if _HAS_IPADDR:
        return ipaddr.IPv4Address(left) in ipaddr.IPv4Network(right)
    else:
        return False


def _in(elt, _list):
    'Helper for match_spec.'
    # build a list from the string or return False
    try:
        lst = eval('(' + _list + ')')
    except Exception:
        return False
    # cast into an int or do nothing
    try:
        elt = int(elt)
    except ValueError:
        pass
    return elt in lst


def match_spec(spec, lines, arr, adder=_adder):
    'Match a line according to a spec and store variables in <var>.'
    # match a line without variable
    for idx in range(len(lines)):
        if lines[idx] == spec:
            del lines[idx]
            return True
    # match a line with a variable, a function or both
    for lidx in range(len(lines)):
        line = lines[lidx]
        varidx = []
        for idx in range(4):
            # try to split the variable and function parts if we have both
            if spec[idx][0] == '$':
                parts = spec[idx].split('=')
                if len(parts) == 2:
                    var, func = parts
                    matched = False
                else:
                    var = func = spec[idx]
            else:
                var = func = spec[idx]
            # Match a function
            if func[-1] == ')':
                res = _FUNC_REGEXP.search(func)
                if res:
                    func_name = '_' + res.group(1)
                    if func_name in globals():
                        if not globals()[func_name](line[idx], res.group(2)):
                            if var == func:
                                break
                        else:
                            if var == func:
                                continue
                            matched = True
                    else:
                        if var == func:
                            break
            # Match a variable
            if ((var == func) or (var != func and matched)) and var[0] == '$':
                if adder == _adder and var[1:] in arr:
                    if arr[var[1:]] != line[idx]:
                        break
                varidx.append((idx, var[1:]))
            # Match the full string
            elif line[idx] != spec[idx]:
                break
        else:
            for i, var in varidx:
                adder(arr, var, line[i])
            del lines[lidx]
            return True
    return False


def match_all(lines, specs, arr, arr2, debug=False):
    '''Match all lines according to a spec and store variables in
<arr>. Variables starting with 2 $ like $$vda are stored in arr and
arr2.'''
    # Work on a copy of lines to avoid changing the real lines because
    # match_spec removes the matched line to not match it again on next
    # calls.
    lines = list(lines)
    for spec in specs:
        if not match_spec(spec, lines, arr):
            if debug:
                sys.stderr.write('spec: %s not matched\n' % str(spec))
            return(False)
    for key in arr:
        if key[0] == '$':
            nkey = key[1:]
            arr[nkey] = arr[key]
            arr2[nkey] = arr[key]
            del arr[key]
    return True


def match_multiple(lines, spec, arr):
    'Use spec to find all the matching lines and gather variables.'
    ret = False
    lines = list(lines)
    while match_spec(spec, lines, arr, adder=_appender):
        ret = True
    return ret

# matcher.py ends here
