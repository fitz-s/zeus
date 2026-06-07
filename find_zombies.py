import os
import re
import ast

src_dir = '/Users/leofitz/zeus/src'
tests_dir = '/Users/leofitz/zeus/tests'

findings = []

def try_parse_block(text_block):
    """
    Attempts to parse a block of text as Python code using ast.parse.
    Tries wrapping/dedenting to handle common fragment syntax errors.
    """
    # Try parsing directly
    try:
        ast.parse(text_block)
        return True
    except SyntaxError:
        pass

    # Try dedenting
    lines = text_block.splitlines()
    if not lines:
        return False
    min_indent = min(len(line) - len(line.lstrip()) for line in lines if line.strip())
    dedented_lines = []
    for line in lines:
        if line.strip():
            dedented_lines.append(line[min_indent:])
        else:
            dedented_lines.append("")
    dedented_block = "\n".join(dedented_lines)
    try:
        ast.parse(dedented_block)
        return True
    except SyntaxError:
        pass

    # Try wrapping in a function (to handle return statements, or indented blocks)
    wrapped_in_func = "def dummy():\n" + "\n".join("    " + line for line in dedented_lines)
    try:
        ast.parse(wrapped_in_func)
        return True
    except SyntaxError:
        pass

    # Try wrapping as an if block (to handle elif/else)
    wrapped_in_if = "if True:\n" + "\n".join("    " + line for line in dedented_lines)
    try:
        ast.parse(wrapped_in_if)
        return True
    except SyntaxError:
        pass

    # Try wrapping inside try/except (to handle except/finally)
    wrapped_in_try = "try:\n    pass\n" + dedented_block
    try:
        ast.parse(wrapped_in_try)
        return True
    except SyntaxError:
        pass

    return False

def is_line_code(line_text):
    """
    Heuristic to check if a single line of comment looks like code.
    We strip the '#' and check if it parses as a valid Python statement/expression,
    or matches specific code patterns.
    """
    content = re.sub(r'^\s*#\s*', '', line_text).strip()
    if not content:
        return False

    # Check if it parses as valid Python statement
    try:
        ast.parse(content)
        # Check that it doesn't parse as a single name (like '# count' or '# index' or '# status')
        # which are common prose descriptions, or docstring-like comments.
        parsed = ast.parse(content)
        if len(parsed.body) == 1 and isinstance(parsed.body[0], ast.Expr):
            expr = parsed.body[0].value
            if isinstance(expr, ast.Name):
                return False
        return True
    except SyntaxError:
        pass

    # Handle common fragment patterns that don't parse on their own
    # e.g., 'if x == 1:', 'elif x == 2:', 'else:', 'try:', 'except Exception:', etc.
    fragment_patterns = [
        r'^if\s+.*:',
        r'^elif\s+.*:',
        r'^else\s*:',
        r'^for\s+.*\s+in\s+.*:',
        r'^while\s+.*:',
        r'^try\s*:',
        r'^except\s*.*:',
        r'^finally\s*:',
        r'^with\s+.*:',
        r'^def\s+[a-zA-Z_]\w*\s*\(',
        r'^class\s+[a-zA-Z_]\w*\s*[:\(]',
    ]
    for pattern in fragment_patterns:
        if re.match(pattern, content):
            return True

    return False

def analyze_file(filepath):
    relpath = os.path.relpath(filepath, '/Users/leofitz/zeus')
    with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
        lines = f.readlines()

    # Phase 1: Commented code blocks (consecutive lines)
    # Group consecutive comment lines
    current_comment_group = []

    def process_comment_group(group):
        if not group:
            return

        # We look for runs within this group that are code
        code_run = []
        for line_num, line_text in group:
            if is_line_code(line_text):
                code_run.append((line_num, line_text))
            else:
                if len(code_run) >= 3:
                    # Let's verify the entire block parses
                    block_text = "".join(re.sub(r'^\s*#\s*', '', x[1]) for x in code_run)
                    if try_parse_block(block_text):
                        record_comment_block(code_run)
                code_run = []
        if len(code_run) >= 3:
            block_text = "".join(re.sub(r'^\s*#\s*', '', x[1]) for x in code_run)
            if try_parse_block(block_text):
                record_comment_block(code_run)

    def record_comment_block(subseg):
        start_line = subseg[0][0]
        end_line = subseg[-1][0]
        lines_cnt = len(subseg)
        snippet = subseg[0][1].strip()

        kind = "COMMENTED_BLOCK"
        content_block = "".join([x[1] for x in subseg])
        if "def " in content_block or "class " in content_block:
            kind = "ZOMBIE_DUP"

        findings.append({
            'file': relpath,
            'range': f"{start_line}-{end_line}",
            'lines': lines_cnt,
            'kind': kind,
            'snippet': snippet,
            'verdict': 'DELETE',
            'risk': 'LOW'
        })

    for idx, line in enumerate(lines):
        line_num = idx + 1
        stripped = line.strip()
        if stripped.startswith('#'):
            current_comment_group.append((line_num, line))
        else:
            process_comment_group(current_comment_group)
            current_comment_group = []
    process_comment_group(current_comment_group)

    # Phase 2: Commented assertions in tests
    if 'tests/' in filepath or 'tests' in relpath:
        for idx, line in enumerate(lines):
            line_num = idx + 1
            stripped = line.strip()
            if stripped.startswith('#'):
                content = re.sub(r'^\s*#\s*', '', line).strip()
                # If it starts with assert or self.assert
                if content.startswith('assert ') or content.startswith('assert(') or content.startswith('self.assert'):
                    # Verify it actually parses as valid Python statement
                    try:
                        ast.parse(content)
                        # Make sure it's not already inside a reported block
                        already_reported = False
                        for f in findings:
                            if f['file'] == relpath:
                                if '-' in f['range']:
                                    start, end = map(int, f['range'].split('-'))
                                else:
                                    start = end = int(f['range'])
                                if start <= line_num <= end:
                                    already_reported = True
                                    break
                        if not already_reported:
                            findings.append({
                                'file': relpath,
                                'range': str(line_num),
                                'lines': 1,
                                'kind': 'DISABLED_ASSERT',
                                'snippet': line.strip(),
                                'verdict': 'REVIEW',
                                'risk': 'HIGH'
                            })
                    except SyntaxError:
                        pass

    # Phase 3: Search for if False / if 0 guards
    if_false_pattern = re.compile(r'^\s*if\s+(False|0)\s*:')
    for idx, line in enumerate(lines):
        line_num = idx + 1
        if if_false_pattern.match(line):
            indent = len(line) - len(line.lstrip())
            block_lines = []
            for j in range(idx + 1, len(lines)):
                next_line = lines[j]
                if next_line.strip() == "":
                    continue
                next_indent = len(next_line) - len(next_line.lstrip())
                if next_indent > indent:
                    block_lines.append(next_line)
                else:
                    break

            is_prov_guard = False
            for bl in block_lines:
                if 'Semantic Provenance Guard' in bl or 'entry_method' in bl or 'selected_method' in bl:
                    is_prov_guard = True
                    break

            findings.append({
                'file': relpath,
                'range': f"{line_num}-{line_num + len(block_lines)}",
                'lines': 1 + len(block_lines),
                'kind': 'IF_FALSE',
                'snippet': line.strip(),
                'verdict': 'REVIEW' if is_prov_guard else 'DELETE',
                'risk': 'LOW' if is_prov_guard else 'MEDIUM'
            })

    # Phase 4: Search for return / pass stubs in functions
    # e.g., 'return  # early-out', 'pass  # TODO'
    # but let's make sure it's inside a function and looks like a debug stub
    stub_pattern = re.compile(r'^\s*(return\s*#.*|pass\s*#\s*TODO.*)')
    for idx, line in enumerate(lines):
        line_num = idx + 1
        if stub_pattern.match(line):
            findings.append({
                'file': relpath,
                'range': str(line_num),
                'lines': 1,
                'kind': 'DEBUG_STUB',
                'snippet': line.strip(),
                'verdict': 'REVIEW',
                'risk': 'LOW'
            })

# Traverse src and tests
for root, dirs, files in os.walk(src_dir):
    for file in files:
        if file.endswith('.py'):
            analyze_file(os.path.join(root, file))

for root, dirs, files in os.walk(tests_dir):
    for file in files:
        if file.endswith('.py'):
            analyze_file(os.path.join(root, file))

# Write output to markdown file
os.makedirs('/Users/leofitz/zeus/.omc/research', exist_ok=True)
report_path = '/Users/leofitz/zeus/.omc/research/slim_commented_code.md'

# Calculate counts
total_lines = 0
total_blocks = 0
disabled_asserts = 0

for f in findings:
    total_lines += f['lines']
    if f['kind'] in ('COMMENTED_BLOCK', 'ZOMBIE_DUP', 'IF_FALSE'):
        total_blocks += 1
    elif f['kind'] == 'DISABLED_ASSERT':
        disabled_asserts += 1

with open(report_path, 'w', encoding='utf-8') as f:
    f.write("# Repo-Slimming Campaign: Commented-Out Code & Zombie Blocks\n\n")
    f.write(f"- **Total Commented-Code/Zombie Lines:** {total_lines}\n")
    f.write(f"- **Total Commented Blocks/Guards:** {total_blocks}\n")
    f.write(f"- **Total Disabled Assertions:** {disabled_asserts}\n\n")

    f.write("## Findings Table\n\n")
    f.write("| File | Line Range | Lines | Kind | Snippet First Line | Verdict | Risk |\n")
    f.write("| --- | --- | --- | --- | --- | --- | --- |\n")
    for row in findings:
        f.write(f"| {row['file']} | {row['range']} | {row['lines']} | {row['kind']} | `{row['snippet']}` | {row['verdict']} | {row['risk']} |\n")

print(f"Report written to {report_path}")
print(f"Total lines: {total_lines}")
print(f"Total blocks/guards: {total_blocks}")
print(f"Disabled assertions: {disabled_asserts}")
