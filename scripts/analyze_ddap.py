import requests
from bs4 import BeautifulSoup
r = requests.get('https://www.ddap.pa.gov/GetHelpNow/Pages/Provider-Directory.aspx', timeout=10)
soup = BeautifulSoup(r.text, 'html.parser')

forms = soup.find_all('form')
print(f'Forms found: {len(forms)}')
for i, form in enumerate(forms):
    print(f'  Form {i}: id={form.get("id")}, action={form.get("action")}, method={form.get("method")}')
    inputs = form.find_all('input')
    for inp in inputs:
        print(f'    input: id={inp.get("id")}, name={inp.get("name")}, type={inp.get("type")}')
    selects = form.find_all('select')
    for sel in selects:
        print(f'    select: id={sel.get("id")}, name={sel.get("name")}')

all_inputs = soup.find_all('input')
print(f'\nAll inputs: {len(all_inputs)}')
for inp in all_inputs[:20]:
    print(f'  id={inp.get("id")}, name={inp.get("name")}, type={inp.get("type")}, class={inp.get("class")}')

buttons = soup.find_all('button')
print(f'\nButtons: {len(buttons)}')
for btn in buttons:
    print(f'  id={btn.get("id")}, class={btn.get("class")}, text={btn.text[:50]}')

links = soup.find_all('a')
print(f'\nLinks with search:')
for link in links:
    if 'search' in link.text.lower() or 'search' in (link.get('href') or '').lower():
        print(f'  {link.text[:50]} -> {link.get("href")}')