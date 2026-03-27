import sys
import os
sys.path.append(os.getcwd())
from backend.app.tools.execution.gee_executor import execute_gee_snippet

code = """
import ee
# Assuming initialized
ee.Initialize(project="hku-geods-gee-project")
fc = ee.FeatureCollection("projects/ee-hku-geog7310/assets/Hong_Kong_District_Boundary")
print("Total districts:", fc.size().getInfo())
bounds = fc.geometry().bounds()
Map.addLayer(fc, {"color": "red"})
"""
res = execute_gee_snippet(code)
print("Result ->", res)
