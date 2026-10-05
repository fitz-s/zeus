"""Offline comparison tests; no HTTP experiment is claimed by these fixtures."""
import importlib.util
import math
from pathlib import Path
import unittest
s=importlib.util.spec_from_file_location('probe',Path(__file__).with_name('round5_public_recheck.py'))
p=importlib.util.module_from_spec(s);s.loader.exec_module(p)

class ComparisonTests(unittest.TestCase):
    def case(self, mismatch=False, paired=True, old=False):
        rows=[];bounds=[];stamp='2026-10-05T08:00:00Z'
        for ch,value,low,high in [('pagasa_metar',31,0,10),('awc',31,20,30),('resolver',32 if mismatch else 31,40,50)]:
            rows.append(dict(station='RPLL',channel=ch,observed_at=stamp if paired or ch!='resolver' else '2026-10-05T07:00:00Z',value=value,unit='C'))
            bounds.append(dict(station='RPLL',channel=ch,observed_at=stamp,positive_receipt_at=str(high),lag_lower_ms=low,lag_upper_ms=high))
        prior=[dict(station='RPLL',channel='pagasa_metar',mismatches=[{'time':'older','match':False}])] if old else []
        return next(r for r in p.compare(rows,bounds,prior,{sid:lambda v:math.floor(v+.5) for sid in p.TARGETS}) if r['station']=='RPLL')
    def test_requires_same_time_value_and_both_comparators(self):
        self.assertEqual(self.case()['verdict'],'ELIGIBLE_VALUE_IDENTICAL_FASTER')
        self.assertEqual(self.case(paired=False)['verdict'],'NO_PAIRED_NATIVE_RESPONSE')
    def test_mismatch_prevents_promotion(self):
        self.assertEqual(self.case(mismatch=True)['verdict'],'PHYSICAL_ONLY_MISMATCH')
    def test_matching_new_window_does_not_erase_prior_contradiction(self):
        self.assertEqual(self.case(old=True)['verdict'],'PHYSICAL_ONLY_MISMATCH')
    def test_empty_window_has_four_finite_residuals(self):
        rows=p.compare([],[],[],{})
        self.assertEqual(len(rows),4)
        self.assertTrue(all(r['verdict']=='NO_PAIRED_NATIVE_RESPONSE' for r in rows))

if __name__=='__main__':unittest.main(verbosity=2)
