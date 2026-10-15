"""Import new artwork without an obsolete gallery and preserve existing data."""
import importlib.util
import json
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]


def test_enroll_preserves_old_rows_and_updates_all_current_consumers(tmp_path):
    spec = importlib.util.spec_from_file_location('import_card_art', ROOT/'tools/import_card_art.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for folder in ('assets/config', 'assets/card_library', 'agent/custom', 'art'):
        (tmp_path/folder).mkdir(parents=True)
    original = dict(templates=np.random.default_rng(1).integers(0,256,(2,32,32,3),dtype=np.uint8),
                    names=np.asarray(['a', 'b']), rarities=np.asarray(['N', 'R']), extra=np.asarray([False,True]))
    np.savez_compressed(tmp_path/'agent/custom/card_fingerprints.npz', **original)
    (tmp_path/'assets/config/card_library.json').write_text(json.dumps({'cards':[{'cardId':'old'}]}), 'utf8')
    (tmp_path/'agent/custom/card_catalog.json').write_text(json.dumps({'entries':{'a':{'name':'a'}}}), 'utf8')
    new = dict(id='new-card', name='新カード', assetId='new-art', upgradeCount=0, rarity=3,
               planType=3, category=1, isConversion=False, unlockProducerLevel=1,
               produceDescriptions=[{'targetId':'CardPlayCount'}])
    metadata = tmp_path/'metadata.json'
    metadata.write_text(json.dumps([new, dict(new,id='hidden',libraryHidden=True),
                                   dict(new,id='upgrade',upgradeCount=1)]), 'utf8')
    Image.new('RGB',(512,512),(80,120,180)).save(tmp_path/'art/new-art.png')
    assert module.enroll(tmp_path, metadata, tmp_path/'art') == ['新カード']
    with np.load(tmp_path/'agent/custom/card_fingerprints.npz') as result:
        for key in original:
            assert np.array_equal(result[key][:-1], original[key]), key
        assert result['names'][-1] == '新カード'
        assert result['rarities'][-1] == 'SR'
        assert result['extra'][-1]
    library = json.loads((tmp_path/'assets/config/card_library.json').read_text('utf8'))
    assert library['cards'][0] == {'cardId':'old'}
    assert len(library['cards']) == 2
    assert (tmp_path/'assets'/library['cards'][1]['image']).is_file()
    catalog = json.loads((tmp_path/'agent/custom/card_catalog.json').read_text('utf8'))
    assert catalog['entries']['a'] == {'name':'a'}
    assert catalog['entries']['新カード'] == {'name':'新カード','rarity':'SR','extra':True}
    assert sorted(p.name for p in (tmp_path/'agent/custom').iterdir()) == ['card_catalog.json','card_fingerprints.npz']
