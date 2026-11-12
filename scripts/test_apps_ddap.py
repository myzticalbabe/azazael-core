from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

opts = Options()
opts.add_argument('--headless=new')
opts.add_argument('--no-sandbox')
opts.add_argument('--disable-dev-shm-usage')
opts.add_argument('--disable-gpu')
opts.add_argument('--window-size=1920,1080')

driver = webdriver.Chrome(options=opts)
try:
    # Try the apps.ddap.pa.gov portal
    driver.get('https://apps.ddap.pa.gov/')
    print(f'Title: {driver.title}')
    print(f'URL: {driver.current_url}')
    
    import time
    time.sleep(5)
    
    # Check for any content
    print(f'Page source length: {len(driver.page_source)}')
    
    # Find all inputs
    inputs = driver.find_elements(By.TAG_NAME, 'input')
    print(f'Inputs: {len(inputs)}')
    for inp in inputs[:10]:
        print(f'  id={inp.get_attribute("id")}, name={inp.get_attribute("name")}, type={inp.get_attribute("type")}, placeholder={inp.get_attribute("placeholder")}')
    
    selects = driver.find_elements(By.TAG_NAME, 'select')
    print(f'Selects: {len(selects)}')
    for sel in selects:
        print(f'  id={sel.get_attribute("id")}, name={sel.get_attribute("name")}')
    
    # Check for login/forms
    forms = driver.find_elements(By.TAG_NAME, 'form')
    print(f'Forms: {len(forms)}')
    for i, form in enumerate(forms):
        print(f'  Form {i}: id={form.get_attribute("id")}, action={form.get_attribute("action")}')
    
    # Look for provider/search links
    links = driver.find_elements(By.TAG_NAME, 'a')
    print(f'Links: {len(links)}')
    for link in links[:20]:
        href = link.get_attribute('href')
        text = link.text.strip()
        if 'provider' in text.lower() or 'search' in text.lower() or 'directory' in text.lower():
            print(f'  {text[:80]} -> {href}')
    
finally:
    driver.quit()