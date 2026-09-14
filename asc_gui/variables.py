"""Resolve source-local declarations without crossing method/block boundaries."""
from dataclasses import dataclass
import re
from .navigation import declarations, identifier_at, mask_literals, closing_paren, split_parameters
from .xref import IDENT, KEYWORDS


@dataclass(frozen=True)
class VariableTarget:
    name: str
    start: int
    end: int
    kind: str
    method_line: int
    type_name: str = ''


TYPE = IDENT + r'(?:\s*\.\s*' + IDENT + r')*(?:\s*<[^;{}=()]*>)?(?:\s*\[\s*\])*'
LOCAL = re.compile(r'(?<![\w$.])(?P<type>' + TYPE + r')\s+(?P<name>' + IDENT + r')(?:\s*\[\s*\])*\s*(?=[=;,:)]|$)')
PRIMITIVES = {'boolean', 'byte', 'char', 'short', 'int', 'long', 'float', 'double'}


def brace_pairs(clean):
    stack, pairs = [], {}
    for pos, char in enumerate(clean):
        if char == '{':
            stack.append(pos)
        elif char == '}' and stack:
            pairs[stack.pop()] = pos
    return pairs


def statement_end(clean, start, braces):
    while start < len(clean) and clean[start].isspace():
        start += 1
    if start < len(clean) and clean[start] == '{':
        return braces.get(start, start)
    # Bound a simple loop body; nested control flow is handled conservatively.
    control = re.match(r'(?:for|while|if|switch|synchronized)\s*\(', clean[start:])
    if control:
        close = closing_paren(clean, start + control.end()-1)
        return statement_end(clean, close+1, braces) if close is not None else start
    depth = 0
    for pos in range(start, len(clean)):
        if clean[pos] in '([':
            depth += 1
        elif clean[pos] in ')]':
            depth -= 1
        elif clean[pos] == ';' and depth == 0:
            return pos
        elif clean[pos] == '}' and depth == 0:
            return pos
    return len(clean)


def find_variable_definition(source, offset, class_name=''):
    token = identifier_at(source, offset)
    if token is None:
        return None
    use_start, use_end, name = token
    clean = mask_literals(source)
    # A member name or invocation is not a source-local variable.
    if clean[:use_start].rstrip().endswith('.') or re.match(r'\s*\(', clean[use_end:]):
        return None
    braces = brace_pairs(clean)
    methods = []
    for row in declarations(source, class_name):
        opening = clean.find('(', row['offset'])
        closing = closing_paren(clean, opening)
        if closing is None:
            continue
        tail = re.match(r'\s*(?:throws\s+[\w.$, \t\n]+)?\s*([;{])', clean[closing+1:])
        if not tail:
            continue
        body = closing+1+tail.end()-1
        end = braces.get(body, body)
        if opening <= use_start <= end:
            methods.append((row, opening, closing, body, end))
    if not methods:
        return None
    row, opening, closing, body, method_end = max(methods, key=lambda item: item[1])
    # Do not resolve a captured outer name through an unparsed lambda/local class.
    # Typed and inferred lambda parameters need their own semantic scope model.
    if re.search(r'->|\bclass\s+', clean[body+1:use_start]):
        return None
    candidates = []
    parameter_search = opening+1
    for parameter in split_parameters(clean[opening+1:closing]):
        begin = clean.find(parameter, parameter_search, closing)
        if begin < 0:
            continue
        parameter_search = begin+len(parameter)
        match = re.search(r'(?P<name>' + IDENT + r')(?:\s*\[\s*\])*\s*$', parameter)
        if match and match['name'] == name:
            start, end = begin+match.start('name'), begin+match.end('name')
            typ = re.sub(r'@[\w.$]+(?:\([^()]*\))?\s*|\bfinal\s+', '', parameter[:match.start('name')]).strip()
            typ += re.sub(r'\s+', '', parameter[match.end('name'):])
            candidates.append((body, start, VariableTarget(name, start, end, 'parameter', row['line'], typ)))
    if body >= method_end or use_start <= closing:
        return candidates[-1][2] if candidates else None
    controls = []
    for match in re.finditer(r'\b(for|catch|try)\s*\(', clean[body+1:method_end]):
        begin = body+1+match.start()
        left = body+1+match.end()-1
        right = closing_paren(clean, left)
        if right is not None:
            controls.append((begin, left, right, statement_end(clean, right+1, braces)))
    # Other function bodies cannot donate local declarations to this function.
    nested_methods = []
    for nested in declarations(source, class_name):
        if row['offset'] < nested['offset'] < method_end:
            left = clean.find('(', nested['offset'])
            right = closing_paren(clean, left)
            if right is not None:
                start_body = clean.find('{', right+1)
                if start_body in braces:
                    nested_methods.append((nested['offset'], braces[start_body]))

    def add(start, end, typ):
        if clean[start:end] != name or start > use_start:
            return
        if any(left <= start <= right for left, right in nested_methods):
            return
        enclosing = [(left, right) for left, right in braces.items() if body <= left < start < right <= method_end]
        if not enclosing:
            return
        scope_start, scope_end = max(enclosing)
        for control_start, left, right, end_body in controls:
            if left < start < right:
                scope_start, scope_end = control_start, end_body
        if scope_start <= use_start <= scope_end:
            suffix = re.match(r'(?:\s*\[\s*\])*', clean[end:]).group()
            candidates.append((scope_start, start, VariableTarget(name, start, end, 'local', row['line'], typ+re.sub(r'\s+', '', suffix))))

    for match in LOCAL.finditer(clean, body+1, method_end):
        typ = match['type'].strip()
        if typ.split()[0] in KEYWORDS - PRIMITIVES:
            continue
        start, end = match.start('name'), match.end('name')
        add(start, end, typ)
        # Additional declarators: int a = call(1, 2), b = 0;
        pos = end
        depth = 0
        while pos < method_end:
            char = clean[pos]
            if char in '([{':
                depth += 1
            elif char in ')]}':
                if depth == 0:
                    break
                depth -= 1
            elif char in ';:' and depth == 0:
                break
            elif char == ',' and depth == 0:
                following = re.match(r'\s*(?P<name>'+IDENT+r')(?:\s*\[\s*\])*\s*(?=[=,;])', clean[pos+1:])
                if following:
                    add(pos+1+following.start('name'), pos+1+following.end('name'), typ)
                else:
                    break
            pos += 1
    return max(candidates, key=lambda item: (item[0],item[1]))[2] if candidates else None
