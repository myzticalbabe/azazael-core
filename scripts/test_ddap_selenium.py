from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By

opts = Options()
opts.add_argument('--headless=new')
opts.add_argument('--no-sandbox')
opts.add_argument('--disable-dev-shm-usage')
opts.add_argument('--disable-gpu')
opts.add_argument('--window-size=1920,1080')

driver = webdriver.Chrome(options=opts)
try:
    driver.get('https://www.ddap.pa.gov/agencies/ddap/ddap-search')
    print(f'Title: {driver.title}')
    print(f'URL: {driver.current_url}')
    
    import time
    time.sleep(5)
    
    inputs = driver.find_elements(By.TAG_NAME, 'input')
    print(f'Inputs: {len(inputs)}')
    for inp in inputs[:10]:
        print(f'  id={inp.get_attribute("id")}, name={inp.get_attribute("name")}, type={inp.get_attribute("type")}, placeholder={inp.get_attribute("placeholder")}')
    
    selects = driver.find_elements(By.TAG_NAME, 'select')
    print(f'Selects: {len(selects)}')
    for sel in selects:
        print(f'  id={sel.get_attribute("id")}, name={sel.get_attribute("name")}')
    
    buttons = driver.find_elements(By.TAG_NAME, 'button')
    print(f'Buttons: {len(buttons)}')
    for btn in buttons[:10]:
        print(f'  id={btn.get_attribute("id")}, text={btn.text[:30]}')
    
    search_elements = driver.find_elements(By.XPATH, "//*[contains(translate(text(), 'SEARCH', 'search'), 'search')]")
    print(f'Search-related elements: {len(search_elements)}')
    for elem in search_elements[:10]:
        print(f'  tag={elem.tag_name}, id={elem.get_attribute("id")}, text={elem.text[:50]}')
finally:
    driver.quit()