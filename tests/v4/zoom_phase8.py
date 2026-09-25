"""Isolated desktop Chrome native-page-zoom smoke against V4 loopback only."""
import os
import math
from pathlib import Path
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

ROOT=Path(__file__).resolve().parents[2]
ARTIFACTS=ROOT/"work"/"phase8"
ARTIFACTS.mkdir(parents=True,exist_ok=True)
os.environ["SE_CACHE_PATH"]=str(ARTIFACTS/"selenium-cache")

options=Options()
options.binary_location=r"C:\Program Files\Google\Chrome\Application\chrome.exe"
options.add_argument("--headless=new")
options.add_argument("--no-first-run")
options.add_argument("--disable-background-networking")
options.add_argument("--window-size=1280,900")
options.add_argument("--user-data-dir="+str(ARTIFACTS/"chrome-zoom-profile-200"))
# Chromium's native partition zoom preference uses level=log(factor)/log(1.2)
# and partition key "x" for the default empty storage partition.
options.add_experimental_option("prefs",{"partition.default_zoom_level":{"x":math.log(2)/math.log(1.2)}})

with webdriver.Chrome(options=options) as driver:
    base="http://127.0.0.1:6004/#/"
    driver.get(base+"dashboard")
    metrics=lambda:driver.execute_script("return {ratio:window.devicePixelRatio,width:window.innerWidth,scroll:document.documentElement.scrollWidth,client:document.documentElement.clientWidth,scale:window.visualViewport.scale}")
    value=metrics()
    print("native_zoom",value)
    assert 1.95<=value["ratio"]<=2.05,"Native Chrome 200% zoom was not achieved"
    assert value["scroll"]<=value["client"],"Whole app overflows at browser zoom 200%"
    print("native_200_percent",value)
    for route,title in (("dashboard","Dashboard"),("library","Library"),("reviews","Needs Review"),("activity","Activity"),("settings","Settings")):
        driver.get(base+route)
        WebDriverWait(driver,10).until(lambda d:d.find_element(By.CSS_SELECTOR,"main h1").text==title)
        current=metrics()
        advanced=driver.execute_script("return document.querySelectorAll('details.advanced[open]').length")
        assert current["ratio"]==2 and current["scroll"]<=current["client"] and advanced==0,(route,current,advanced)
        print("page",route,"zoom200",current["client"],"scroll",current["scroll"],"advanced_open",advanced)
    driver.get(base+"reviews?state=resolved&q=Sailor%20Moon%20Crystal")
    WebDriverWait(driver,10).until(lambda d:"confine 14/12 non è verificabile" in d.find_element(By.CSS_SELECTOR,"main").text)
    current=metrics()
    assert current["scroll"]<=current["client"]
    driver.save_screenshot(str(ARTIFACTS/"native-zoom-200-crystal.png"))
    print("crystal_review_zoom200",current["client"],"scroll",current["scroll"])
