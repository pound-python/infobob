from pygments import highlight
from pygments.filter import Filter
from pygments.formatters import NullFormatter
from pygments.lexers import PythonLexer
from pygments.token import Token

class _RedentFilter(Filter):
    def filter(self, lexer, stream):
        indent = 0
        cruft_stack = []
        eat_whitespace = False
        for ttype, value in stream:
            if eat_whitespace:
                if ttype is Token.Text and value.isspace():
                    continue
                elif ttype is Token.Punctuation and value == ';':
                    indent -= 1
                    continue
                else:
                    yield Token.Text, '    ' * indent
                    eat_whitespace = False
            if ttype is Token.Punctuation:
                if value == '{':
                    cruft_stack.append('brace')
                elif value == '}':
                    assert cruft_stack.pop() == 'brace'
                elif value == '[':
                    cruft_stack.append('bracket')
                elif value == ']':
                    assert cruft_stack.pop() == 'bracket'
                elif value == ':':
                    if cruft_stack and cruft_stack[-1] == 'lambda':
                        cruft_stack.pop()
                    elif not cruft_stack:
                        indent += 1
                        yield ttype, value
                        yield Token.Text, '\n'
                        eat_whitespace = True
                        continue
                elif value == ';':
                    yield Token.Text, '\n'
                    eat_whitespace = True
                    continue
            elif ttype is Token.Keyword and value == 'lambda':
                cruft_stack.append('lambda')
            yield ttype, value

def redent(s):
    lexer = PythonLexer()
    lexer.add_filter(_RedentFilter())
    return highlight(s, lexer, NullFormatter())
