#!/usr/bin/env python3
# Copyright (c) 2026 Innovation Trigger B.V.
#
# PDFluent is available under two licences, at your option: the GNU AGPLv3, or
# the PDFluent Commercial Licence. See the LICENSE file in this repository --
# that file travels with the copy you received, which a URL does not.
"""Every FinTabNet company lands on exactly one side of the split (#574).

The structured lane measures only the eval side and the training dump (a later
spec) takes only the train side, both through `fintabnet_split.side()`. A rule
that moved a company would either tune on the rig or measure on the training
set, and neither would report itself -- so the split is frozen here, all 428
companies of IBM's release with the 41 the rule puts on the eval side.

The fixture is the company directories of `archive.zip` at the revision
DATASETS.toml pins; the 41 are what `sha256(company) % 10 == 0` gives for
them (10,034 pages), the same rule as
`gs://pdfluent-datasets/fintabnet/1.0.0/fintabnet_split_by_company.json`.
"""
from __future__ import annotations

import pathlib
import random
import sys
import unittest

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "scripts" / "extraction"))

import fintabnet_split  # noqa: E402

# Every company directory of fintabnet/pdf/ in the pinned archive.
ALL_COMPANIES = [
    'A', 'AAL', 'AAP', 'AAPL', 'ABBV', 'ABC', 'ABMD', 'ABT', 'ACN', 'ADBE',
    'ADI', 'ADM', 'ADP', 'ADS', 'ADSK', 'AEE', 'AES', 'AFL', 'AIG', 'AIV',
    'AIZ', 'AJG', 'AKAM', 'ALB', 'ALGN', 'ALK', 'ALL', 'ALLE', 'ALXN',
    'AMAT', 'AMCR', 'AMD', 'AME', 'AMGN', 'AMP', 'AMT', 'AMZN', 'ANET',
    'ANSS', 'ANTM', 'AON', 'AOS', 'APA', 'APD', 'APH', 'APTV', 'ARE', 'ATO',
    'ATVI', 'AVB', 'AVY', 'AWK', 'AXP', 'BA', 'BAC', 'BAX', 'BBY', 'BDX',
    'BEN', 'BIIB', 'BKNG', 'BKR', 'BLK', 'BLL', 'BMY', 'BR', 'BSX', 'BWA',
    'BXP', 'C', 'CAG', 'CAH', 'CAT', 'CB', 'CBOE', 'CBRE', 'CCI', 'CCL',
    'CDNS', 'CDW', 'CE', 'CERN', 'CF', 'CFG', 'CHD', 'CHRW', 'CHTR', 'CI',
    'CINF', 'CL', 'CMA', 'CMCSA', 'CME', 'CMG', 'CMI', 'CMS', 'CNC', 'CNP',
    'D', 'DAL', 'DE', 'DFS', 'DG', 'DGX', 'DHI', 'DHR', 'DIS', 'DISCA',
    'DISH', 'DLR', 'DLTR', 'DOV', 'DPZ', 'DRE', 'DRI', 'DUK', 'DVA', 'DVN',
    'DXC', 'EA', 'EBAY', 'ECL', 'ED', 'EFX', 'EIX', 'EL', 'EMN', 'EMR',
    'EOG', 'EQIX', 'EQR', 'ES', 'ESS', 'ETFC', 'ETN', 'ETR', 'EVRG', 'EW',
    'EXC', 'EXPD', 'EXPE', 'EXR', 'F', 'FAST', 'FB', 'FBHS', 'FCX', 'FDX',
    'FE', 'FFIV', 'FIS', 'FISV', 'FITB', 'FLIR', 'FLS', 'FLT', 'FMC', 'FOX',
    'FRT', 'FTI', 'FTNT', 'FTV', 'GD', 'GE', 'GIS', 'GL', 'GLW', 'GM',
    'GPC', 'GPN', 'GPS', 'GRMN', 'GS', 'HAL', 'HAS', 'HBAN', 'HBI', 'HCA',
    'HD', 'HES', 'HFC', 'HIG', 'HII', 'HLT', 'HOG', 'HOLX', 'HON', 'HPE',
    'HPQ', 'HRB', 'HRL', 'HSIC', 'HST', 'HSY', 'HUM', 'HWM', 'ICE', 'IDXX',
    'IEX', 'IFF', 'ILMN', 'INCY', 'INFO', 'INTC', 'INTU', 'IP', 'IPG',
    'IPGP', 'IQV', 'IRM', 'ISRG', 'IT', 'ITW', 'IVZ', 'J', 'JBHT', 'JCI',
    'JKHY', 'JNJ', 'JNPR', 'JPM', 'K', 'KEY', 'KEYS', 'KHC', 'KIM', 'KLAC',
    'KMB', 'KMI', 'KMX', 'KO', 'KR', 'KSS', 'KSU', 'L', 'LB', 'LDOS', 'LEG',
    'LEN', 'LH', 'LHX', 'LKQ', 'LLY', 'LMT', 'LNC', 'MA', 'MAA', 'MAR',
    'MAS', 'MCD', 'MCHP', 'MCK', 'MCO', 'MDLZ', 'MDT', 'MET', 'MGM', 'MHK',
    'MKC', 'MKTX', 'MMC', 'MMM', 'MNST', 'MO', 'MOS', 'MPC', 'MRK', 'MRO',
    'MS', 'MSCI', 'MSFT', 'MSI', 'MTB', 'MTD', 'MU', 'MXIM', 'NBL', 'NCLH',
    'NDAQ', 'NEE', 'NEM', 'NFLX', 'NKE', 'NLOK', 'NLSN', 'NOC', 'NOV',
    'NRG', 'NSC', 'NTAP', 'NTRS', 'NVDA', 'NVR', 'NWL', 'NWS', 'O', 'ODFL',
    'OKE', 'OMC', 'ORCL', 'ORLY', 'OXY', 'PAYC', 'PAYX', 'PBCT', 'PCAR',
    'PEAK', 'PEP', 'PFE', 'PFG', 'PG', 'PGR', 'PH', 'PHM', 'PKG', 'PKI',
    'PM', 'PNC', 'PNR', 'PNW', 'PPG', 'PPL', 'PRGO', 'PRU', 'PSA', 'PSX',
    'PVH', 'PWR', 'PXD', 'QCOM', 'QRVO', 'RCL', 'RE', 'REG', 'REGN', 'RF',
    'RHI', 'RJF', 'RL', 'RMD', 'ROK', 'ROL', 'ROP', 'ROST', 'RSG', 'SBAC',
    'SBUX', 'SCHW', 'SEE', 'SIVB', 'SJM', 'SLB', 'SLG', 'SNA', 'SNPS', 'SO',
    'SPG', 'SPGI', 'STE', 'STT', 'STX', 'STZ', 'SWK', 'SWKS', 'SYF', 'SYK',
    'SYY', 'TAP', 'TDG', 'TEL', 'TFX', 'TGT', 'TIF', 'TJX', 'TMO', 'TMUS',
    'TROW', 'TRV', 'TSCO', 'TSN', 'TTWO', 'TWTR', 'TXN', 'TXT', 'UA', 'UAA',
    'UDR', 'UHS', 'ULTA', 'UNH', 'UNM', 'UNP', 'UPS', 'URI', 'V', 'VAR',
    'VFC', 'VLO', 'VMC', 'VNO', 'VRSK', 'VRSN', 'VRTX', 'VTR', 'WAB', 'WAT',
    'WBA', 'WDC', 'WEC', 'WELL', 'WHR', 'WLTW', 'WM', 'WMB', 'WMT', 'WRB',
    'WRK', 'WST', 'WU', 'WY', 'XEL', 'XLNX', 'XRAY', 'XYL', 'YUM', 'ZBH',
    'ZBRA', 'ZION',
]

# The 41 the rule puts on the eval side.
EVAL_COMPANIES = [
    'AFL', 'ATO', 'BA', 'CDNS', 'CDW', 'CHRW', 'CMS', 'DIS', 'DVN', 'EFX',
    'ES', 'ETR', 'EXPE', 'FCX', 'FDX', 'FFIV', 'FLIR', 'FTV', 'GLW', 'HSY',
    'INTU', 'JNJ', 'KIM', 'LH', 'MAA', 'MCHP', 'MET', 'MKTX', 'MNST', 'MO',
    'NLOK', 'PSX', 'PXD', 'RJF', 'SCHW', 'TDG', 'TIF', 'VLO', 'VRSN',
    'WHR', 'WM',
]


class SplitTests(unittest.TestCase):
    def test_the_eval_set_is_exactly_the_41_companies_of_the_rule(self):
        got = sorted(c for c in ALL_COMPANIES
                     if fintabnet_split.side(c) == "eval")
        self.assertEqual(got, sorted(EVAL_COMPANIES))

    def test_no_company_is_on_both_sides(self):
        for company in ALL_COMPANIES:
            self.assertIn(fintabnet_split.side(company), ("eval", "train"),
                          company)
        eval_side = {c for c in ALL_COMPANIES
                     if fintabnet_split.side(c) == "eval"}
        train_side = {c for c in ALL_COMPANIES
                      if fintabnet_split.side(c) == "train"}
        self.assertEqual(eval_side & train_side, set())
        self.assertEqual(eval_side | train_side, set(ALL_COMPANIES))

    def test_side_depends_only_on_the_name(self):
        # The same name, asked repeatedly and in any order, always lands on
        # the same side: there is no year, no page and no state in the answer.
        first = {c: fintabnet_split.side(c) for c in ALL_COMPANIES}
        order = list(ALL_COMPANIES)
        random.Random(574).shuffle(order)
        for company in order:
            self.assertEqual(fintabnet_split.side(company), first[company])
            self.assertEqual(fintabnet_split.side(company), first[company])


if __name__ == "__main__":
    unittest.main()
