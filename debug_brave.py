#!/usr/bin/env python3
"""Debug BraveEngine parsing."""

import re

# Read the fixture
with open('tests/fixtures/websearch/brave_results.html', 'r') as f:
    html_content = f.read()

print(f"HTML length: {len(html_content)}")

# Test container extraction
result_containers = re.findall(
    r'data-testid="result"[^>]*>(.*?)(?=data-testid="result"|$)',
    html_content,
    re.DOTALL | re.IGNORECASE
)
print(f'Containers found: {len(result_containers)}')

if result_containers:
    container = result_containers[0]
    print(f'Container length: {len(container)}')
    print('First 500 chars:')
    print(container[:500])
    print('---')

    # Test the exact regex from BraveEngine
    title_match = re.search(
        r'data-testid="result-title-link"[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
        result_containers[0], re.DOTALL | re.IGNORECASE
    )
    print(f'Title match: {title_match}')
    if title_match:
        print(f'  URL: {title_match.group(1)}')
        print(f'  Title: {title_match.group(2)[:100]}')
    else:
        print('No title match found')

        # Try alternative patterns
        print('--- Trying alternative patterns ---')

        # Try with escaped quotes
        alt1 = re.search(
            r'data-testid="result-title-link"[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
            result_containers[0], re.DOTALL | re.IGNORECASE
        )
        print(f'Alt1 match: {alt1}')

        # Try with single quotes
        alt2 = re.search(
            r"data-testid='result-title-link'[^>]*href='([^']*)'[^>]*>(.*?)</a>",
            result_containers[0], re.DOTALL | re.IGNORECASE
        )
        print(f'Alt2 match: {alt2}')

        # Try with escaped quotes in regex
        alt3 = re.search(
            r'data-testid="result-title-link"[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
            result_containers[0], re.DOTALL | re.IGNORECASE
        )
        print(f'Alt3 match: {alt3}')

        # Check the raw HTML around the title
        import re
        idx = result_containers[0].find('result-title-link')
        if idx >= 0:
            print(f'Found at index {idx}:')
            print(result_containers[0][idx:idx+300])