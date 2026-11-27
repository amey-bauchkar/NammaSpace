# Corridor capture QA — 27 November 2026

Windows, Python 3.14, Intel graphics. CPU archive QA only. Source archive
21_11_2026.zip, 205972268 bytes. 1286 frame IDs, 3 incomplete; chronological
split 1025 train, 129 development, 129 reserved. Inspected only training RGB
and depth for QA. Of these, 298 fell below the existing sharpness_peak floor of
35. All 1025 lacked usable depth under the reader's confidence convention.
The first frame failed with confidence values outside 0/54/255. Do not use
these maps as measured LiDAR. Status INVESTIGATE. Surface coverage unmeasured.

From repository root in PowerShell:

    $env:PYTHONPATH='src'
    py -3.14 -m namma.capture.capture_qa C:/Users/SEBIN/Downloads/21_11_2026.zip
