"""Offline reference-library regression; not a real-frame accuracy benchmark."""
import io
import json
import subprocess
from pathlib import Path

import numpy as np

ROOT=Path(__file__).resolve().parents[1]
IDS={'p_card-01-ido-3_240','p_card-01-ido-3_242','p_card-01-ido-3_244',
     'p_card-01-ido-3_246','p_card-01-sup-3_253','p_card-02-ido-3_243',
     'p_card-02-ido-3_248','p_card-03-ido-3_245','p_card-03-ido-3_247','p_card-03-sup-3_252'}


def test_new_cards_reach_all_shared_galleries():
    library=json.loads((ROOT/'assets/config/card_library.json').read_text('utf8'))['cards']
    catalog=json.loads((ROOT/'agent/custom/card_catalog.json').read_text('utf8'))['entries']
    cards=[c for c in library if c['cardId'] in IDS]
    assert len(cards)==10
    with np.load(ROOT/'agent/custom/card_fingerprints.npz') as hand:
        for c in cards:
            assert (ROOT/'assets'/c['image']).is_file()
            assert catalog[c['key']]['name']==c['name']
            assert c['name'] in hand['names']
            assert hand['extra'][list(hand['names']).index(c['name'])]==c['extra']


def test_prior_reference_rows_are_unchanged():
    for path in ['agent/custom/card_fingerprints.npz']:
        raw=subprocess.check_output(['git','show','v2.0.1:'+path],cwd=ROOT)
        with np.load(io.BytesIO(raw)) as old,np.load(ROOT/path) as new:
            for key in old.files:
                assert np.array_equal(old[key],new[key][:len(old[key])]),key
