# The MIT License
#
# Copyright (c) 2009-2012 the bpython authors.
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.
#

from __future__ import with_statement
import __builtin__
import __main__
import rlcompleter
import line as lineparts
import re
import os
from glob import glob

import jedi

from bpython import inspection
from bpython import importcompletion
from bpython._py3compat import py3

# Needed for special handling of __abstractmethods__
# abc only exists since 2.6, so check both that it exists and that it's
# the one we're expecting
try:
    import abc
    abc.ABCMeta
    has_abc = True
except (ImportError, AttributeError):
    has_abc = False

# Autocomplete modes
SIMPLE = 'simple'
SUBSTRING = 'substring'
FUZZY = 'fuzzy'

MAGIC_METHODS = ["__%s__" % s for s in [
    "init", "repr", "str", "lt", "le", "eq", "ne", "gt", "ge", "cmp", "hash",
    "nonzero", "unicode", "getattr", "setattr", "get", "set", "call", "len",
    "getitem", "setitem", "iter", "reversed", "contains", "add", "sub", "mul",
    "floordiv", "mod", "divmod", "pow", "lshift", "rshift", "and", "xor", "or",
    "div", "truediv", "neg", "pos", "abs", "invert", "complex", "int", "float",
    "oct", "hex", "index", "coerce", "enter", "exit"]]


def after_last_dot(name):
    return name.rstrip('.').rsplit('.')[-1]

def get_completer(completers, cursor_offset, line, **kwargs):
    """Returns a list of matches and an applicable completer

    If no matches available, returns a tuple of an empty list and None

    kwargs (all required):
        cursor_offset is the current cursor column
        line is a string of the current line
        locals_ is a dictionary of the environment
        argspec is an inspect.ArgSpec instance for the current function where
            the cursor is
        current_block is the possibly multiline not-yet-evaluated block of
            code which the current line is part of
        mode is one of SIMPLE, SUBSTRING or FUZZY - ways to find matches
        complete_magic_methods is a bool of whether we ought to complete
            double underscore methods like __len__ in method signatures
    """

    for completer in completers:
        matches = completer.matches(cursor_offset, line, **kwargs)
        if matches is not None:
            return matches, (completer if matches else None)
    return [], None

def get_completer_bpython(**kwargs):
    """"""
    return get_completer([DictKeyCompletion,
                          StringLiteralAttrCompletion,
                          ImportCompletion,
                          FilenameCompletion,
                          MagicMethodCompletion,
                          MultilineJediCompleter,
                          GlobalCompletion,
                          CumulativeCompleter([AttrCompletion, ParameterNameCompletion])],
                         **kwargs)

class BaseCompletionType(object):
    """Describes different completion types"""
    @classmethod
    def matches(cls, cursor_offset, line, **kwargs):
        """Returns a list of possible matches given a line and cursor, or None
        if this completion type isn't applicable.

        ie, import completion doesn't make sense if there cursor isn't after
        an import or from statement, so it ought to return None.

        Completion types are used to:
            * `locate(cur, line)` their initial target word to replace given a line and cursor
            * find `matches(cur, line)` that might replace that word
            * `format(match)` matches to be displayed to the user
            * determine whether suggestions should be `shown_before_tab`
            * `substitute(cur, line, match)` in a match for what's found with `target`
            """
        raise NotImplementedError
    @classmethod
    def locate(cls, cursor_offset, line):
        """Returns a start, stop, and word given a line and cursor, or None
        if no target for this type of completion is found under the cursor"""
        raise NotImplementedError
    @classmethod
    def format(cls, word):
        return word
    shown_before_tab = True # whether suggestions should be shown before the
                            # user hits tab, or only once that has happened
    def substitute(cls, cursor_offset, line, match):
        """Returns a cursor offset and line with match swapped in"""
        start, end, word = cls.locate(cursor_offset, line)
        result = start + len(match), line[:start] + match + line[end:]
        return result

class CumulativeCompleter(object):
    """Returns combined matches from several completers"""
    def __init__(self, completers):
        if not completers:
            raise ValueError("CumulativeCompleter requires at least one completer")
        self._completers = completers
        self.shown_before_tab = True

    @property
    def locate(self):
        return self._completers[0].locate if self._completers else lambda *args: None

    @property
    def format(self):
        return self._completers[0].format if self._completers else lambda s: s

    def matches(self, cursor_offset, line, locals_, argspec, current_block,
                complete_magic_methods, history):
        all_matches = []
        for completer in self._completers:
            # these have to be explicitely listed to deal with the different
            # signatures of various matches() methods of completers
            matches = completer.matches(cursor_offset=cursor_offset,
                                        line=line,
                                        locals_=locals_,
                                        argspec=argspec,
                                        current_block=current_block,
                                        complete_magic_methods=complete_magic_methods)
            if matches is not None:
                all_matches.extend(matches)

        return sorted(set(all_matches))


class ImportCompletion(BaseCompletionType):
    @classmethod
    def matches(cls, cursor_offset, line, **kwargs):
        return importcompletion.complete(cursor_offset, line)
    locate = staticmethod(lineparts.current_word)
    format = staticmethod(after_last_dot)

class FilenameCompletion(BaseCompletionType):
    shown_before_tab = False
    @classmethod
    def matches(cls, cursor_offset, line, **kwargs):
        cs = lineparts.current_string(cursor_offset, line)
        if cs is None:
            return None
        start, end, text = cs
        matches = []
        username = text.split(os.path.sep, 1)[0]
        user_dir = os.path.expanduser(username)
        for filename in glob(os.path.expanduser(text + '*')):
            if os.path.isdir(filename):
                filename += os.path.sep
            if text.startswith('~'):
                filename = username + filename[len(user_dir):]
            matches.append(filename)
        return matches

    locate = staticmethod(lineparts.current_string)
    @classmethod
    def format(cls, filename):
        filename.rstrip(os.sep).rsplit(os.sep)[-1]
        if os.sep in filename[:-1]:
            return filename[filename.rindex(os.sep, 0, -1)+1:]
        else:
            return filename

class AttrCompletion(BaseCompletionType):
    @classmethod
    def matches(cls, cursor_offset, line, locals_, **kwargs):
        r = cls.locate(cursor_offset, line)
        if r is None:
            return None
        text = r[2]

        if locals_ is None:
            locals_ = __main__.__dict__

        assert '.' in text

        for i in range(1, len(text) + 1):
            if text[-i] == '[':
                i -= 1
                break
        methodtext = text[-i:]
        matches = [''.join([text[:-i], m]) for m in
                            attr_matches(methodtext, locals_)]

        #TODO add open paren for methods via _callable_prefix (or decide not to)
        # unless the first character is a _ filter out all attributes starting with a _
        if not text.split('.')[-1].startswith('_'):
            matches = [match for match in matches
                       if not match.split('.')[-1].startswith('_')]
        return matches

    locate = staticmethod(lineparts.current_dotted_attribute)
    format = staticmethod(after_last_dot)

class DictKeyCompletion(BaseCompletionType):
    locate = staticmethod(lineparts.current_dict_key)
    @classmethod
    def matches(cls, cursor_offset, line, locals_, **kwargs):
        r = cls.locate(cursor_offset, line)
        if r is None:
            return None
        start, end, orig = r
        _, _, dexpr = lineparts.current_dict(cursor_offset, line)
        try:
            obj = safe_eval(dexpr, locals_)
        except EvaluationError:
            return []
        if obj and isinstance(obj, type({})) and obj.keys():
            return ["{!r}]".format(k) for k in obj.keys() if repr(k).startswith(orig)]
        else:
            return []
    @classmethod
    def format(cls, match):
        return match[:-1]

class MagicMethodCompletion(BaseCompletionType):
    locate = staticmethod(lineparts.current_method_definition_name)
    @classmethod
    def matches(cls, cursor_offset, line, current_block, **kwargs):
        r = cls.locate(cursor_offset, line)
        if r is None:
            return None
        if 'class' not in current_block:
            return None
        start, end, word = r
        return [name for name in MAGIC_METHODS if name.startswith(word)]

class GlobalCompletion(BaseCompletionType):
    @classmethod
    def matches(cls, cursor_offset, line, locals_, **kwargs):
        """Compute matches when text is a simple name.
        Return a list of all keywords, built-in functions and names currently
        defined in self.namespace that match.
        """
        r = cls.locate(cursor_offset, line)
        if r is None:
            return None
        start, end, text = r

        hash = {}
        n = len(text)
        import keyword
        for word in keyword.kwlist:
            if method_match(word, n, text):
                hash[word] = 1
        for nspace in [__builtin__.__dict__, locals_]:
            for word, val in nspace.items():
                if method_match(word, len(text), text) and word != "__builtins__":
                    hash[_callable_postfix(val, word)] = 1
        matches = hash.keys()
        matches.sort()
        return matches

    locate = staticmethod(lineparts.current_single_word)

class ParameterNameCompletion(BaseCompletionType):
    @classmethod
    def matches(cls, cursor_offset, line, argspec, **kwargs):
        if not argspec:
            return None
        r = cls.locate(cursor_offset, line)
        if r is None:
            return None
        start, end, word = r
        if argspec:
            matches = [name + '=' for name in argspec[1][0]
                       if isinstance(name, basestring) and name.startswith(word)]
            if py3:
                matches.extend(name + '=' for name in argspec[1][4]
                               if name.startswith(word))
        return matches
    locate = staticmethod(lineparts.current_word)

class StringLiteralAttrCompletion(BaseCompletionType):
    locate = staticmethod(lineparts.current_string_literal_attr)
    @classmethod
    def matches(cls, cursor_offset, line, **kwargs):
        r = cls.locate(cursor_offset, line)
        if r is None:
            return None
        start, end, word = r
        attrs = dir('')
        matches = [att for att in attrs if att.startswith(word)]
        if not word.startswith('_'):
            return [match for match in matches if not match.startswith('_')]
        return matches

class JediCompleter(BaseCompletionType):
    @classmethod
    def matches(cls, cursor_offset, line, history, **kwargs):
        if not lineparts.current_word(cursor_offset, line):
            return None
        history = '\n'.join(history) + '\n' + line
        script = jedi.Script(history, len(history.splitlines()), cursor_offset, 'fake.py')
        completions = script.completions()
        if not completions:
            cls.locate = None
            return []

        @classmethod
        def locate_original(cls, cursor_offset, line):
            start = cursor_offset - (len(completions[0].name) - len(completions[0].complete))
            end = cursor_offset
            return start, end, line[start:end]

        cls.locate = locate_original

        matches = [c.name for c in completions]
        if all(m.startswith('_') for m in matches):
            return matches
        elif any(not m.startswith(matches[0][0]) for m in matches):
            return None
        else:
            return [m for m in matches if not m.startswith('_')]

class MultilineJediCompleter(JediCompleter):
    @classmethod
    def matches(cls, cursor_offset, line, current_block, history, **kwargs):
        if '\n' in current_block:
            return JediCompleter.matches(cursor_offset, line, history)
        else:
            return None

class EvaluationError(Exception):
    """Raised if an exception occurred in safe_eval."""


def safe_eval(expr, namespace):
    """Not all that safe, just catches some errors"""
    try:
        return eval(expr, namespace)
    except (NameError, AttributeError, SyntaxError):
        # If debugging safe_eval, raise this!
        # raise
        raise EvaluationError


def attr_matches(text, namespace):
    """Taken from rlcompleter.py and bent to my will.
    """

    # Gna, Py 2.6's rlcompleter searches for __call__ inside the
    # instance instead of the type, so we monkeypatch to prevent
    # side-effects (__getattr__/__getattribute__)
    m = re.match(r"(\w+(\.\w+)*)\.(\w*)", text)
    if not m:
        return []

    expr, attr = m.group(1, 3)
    if expr.isdigit():
        # Special case: float literal, using attrs here will result in
        # a SyntaxError
        return []
    try:
        obj = safe_eval(expr, namespace)
    except EvaluationError:
        return []
    with inspection.AttrCleaner(obj):
        matches = attr_lookup(obj, expr, attr)
    return matches

def attr_lookup(obj, expr, attr):
    """Second half of original attr_matches method factored out so it can
    be wrapped in a safe try/finally block in case anything bad happens to
    restore the original __getattribute__ method."""
    words = dir(obj)
    if hasattr(obj, '__class__'):
        words.append('__class__')
        words = words + rlcompleter.get_class_members(obj.__class__)
        if has_abc and not isinstance(obj.__class__, abc.ABCMeta):
            try:
                words.remove('__abstractmethods__')
            except ValueError:
                pass

    matches = []
    n = len(attr)
    for word in words:
        if method_match(word, n, attr) and word != "__builtins__":
            matches.append("%s.%s" % (expr, word))
    return matches

def _callable_postfix(value, word):
    """rlcompleter's _callable_postfix done right."""
    with inspection.AttrCleaner(value):
        if inspection.is_callable(value):
            word += '('
    return word

def method_match(word, size, text):
    return word[:size] == text
