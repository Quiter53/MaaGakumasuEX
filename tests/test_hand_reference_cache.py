"""Frozen pre-optimization scorer vs cache, including exact floating results."""
import numpy as np
import pytest
from custom.hand_identity import HandIdentityReader, visible_mask

class UncachedReference(HandIdentityReader):
    def rank(self, target, mask, mode):
        templates, names, _, _ = self.model
        # Pool evidence within local patches for occluded cards; global
        # normalization on whole artwork for unobscured cards.
        regions = [mask] if mode == "full" else []
        if mode == "local":
            for yy in (4, 12, 20):
                for xx in (4, 12, 20):
                    part = np.zeros((32, 32), bool)
                    part[yy : yy + 8, xx : xx + 8] = mask[yy : yy + 8, xx : xx + 8]
                    if part.sum() >= 24:
                        regions.append(part)
        if not regions:
            return None
        scores, weights = [], []
        for region in regions:
            query = target[region]
            refs = templates[:, region, :]
            # Preserve color as well as structure; flat shared backgrounds
            # supply little information and must not dominate identification.
            q = query - query.mean(axis=0)
            r = refs - refs.mean(axis=1, keepdims=True)
            numerator = (r * q).sum(axis=(1, 2))
            denominator = np.sqrt((r * r).sum(axis=(1, 2)) * (q * q).sum()) + 1e-6
            corr = numerator / denominator
            distance = ((refs - query) ** 2).mean(axis=(1, 2))
            scores.append(corr - 0.5 * distance)
            weights.append(max(0.005, float(query.std(axis=0).mean())) * region.sum())
        values = np.average(np.asarray(scores), axis=0, weights=weights)
        index = int(np.argmax(values))
        different = names != names[index]
        second = float(values[different].max()) if different.any() else -1.0
        return str(names[index]), float(values[index]), float(values[index] - second)


@pytest.mark.parametrize("mode", ["full", "local"])
def test_cached_stats_preserve_scores_for_new_queries_masks_and_models(mode):
    rng = np.random.default_rng(401)
    cached, original = HandIdentityReader(), UncachedReference()
    templates = rng.random((8,32,32,3), dtype=np.float32)
    names = np.array(["a","b","c","d"]*2)
    for model in (templates, templates.copy()*0.8):
        cached.model = original.model = (model,names,None,None)
        for width in range(18,32):
            mask = visible_mask([19,884,189,249],[[19,884,189,249]])
            mask[:,width:] = False
            for repeat in range(2):
                query = rng.random((32,32,3), dtype=np.float32)
                assert cached.rank(query,mask,mode) == original.rank(query,mask,mode)
            assert len(cached._reference_cache) <= 8
        assert cached._reference_model is model


def test_mutating_input_mask_cannot_mutate_cached_region():
    reader = HandIdentityReader()
    mask = np.ones((32,32), bool)
    original = mask.copy()
    regions = reader._reference_regions(reader.model[0], mask, "full")
    mask[:16] = False
    assert np.array_equal(regions[0][0], original)
