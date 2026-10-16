"""Transaction-local deck observations; never reuse screen coordinates across pages."""
import numpy as np

from .card_visual import _resize_rgb_bilinear, intersection_over_union

DECK_IDENTITY_MIN_CONFIDENCE = .80


def deck_card_disabled(image, box):
    """Detect the dark veil over an otherwise identified square deck tile.

    Use the whole inner tile, including white effect/cost markings, rather
    than artwork colour (the enabled sleep card is naturally gray). A dark
    illustration still has bright UI ink. Ignore brackets outside the tile.
    This is current-frame availability, never a permanent property of a name.
    """
    if image is None or image.ndim != 3:
        return False
    x, y, w, h = map(int, box)
    if min(w, h) < 80 or min(x, y) < 0 or x+w > image.shape[1] or y+h > image.shape[0]:
        return False
    inset = max(7, round(min(w, h)*.07))
    value = image[y+inset:y+h-inset, x+inset:x+w-inset, :3].max(axis=2)
    # Enabled white strokes are ~255; the observed disabled overlay caps
    # even white ink at ~128. Keep a wide gap and reject empty/black crops.
    return bool(45 <= np.quantile(value, .98) <= 165
                and np.quantile(value, .999) <= 190 and value.std() >= 8)


def deck_scroll_distance(boxes, *, top=620, overlap=False):
    """At most three four-card rows; align a clipped view before advancing.

    A three-row page advances 12 cards. Partial views advance less so that
    the next unseen row becomes fully visible. Copy counting retains one
    full row of overlap, required by its existing duplicate-proof tracker.
    """
    rows = []
    for x, y, w, h in sorted(boxes, key=lambda b: b[1]):
        if not (top <= y and y+h <= 1100 and 90 <= h <= 145):
            continue
        row = next((r for r in rows if abs(np.median(r)-y) <= 10), None)
        if row is None:
            rows.append([y])
        else:
            row.append(y)
    ys = [float(np.median(row)) for row in rows]
    gaps = [(b-a)/round((b-a)/146) for a, b in zip(ys, ys[1:]) if round((b-a)/146) >= 1]
    pitch = float(np.median(gaps)) if gaps and all(136 <= g <= 156 for g in gaps) else 146.
    limit = (2 if overlap else 3)*pitch
    if not ys:
        return round(min(limit, 2*pitch))
    # The last complete row bounds the scanned area even if a detector
    # missed earlier rows. Four-edge row completion runs before this point.
    distance = ys[-1] + (0 if overlap else pitch) - (top+18)
    return round(max(60, min(limit, distance, 446)))


def removal_priority_bound(item, blacklist, replacement, *, change=False):
    """Best tier an unnamed tile can have; frame evidence never proves a name."""
    frame = item.get('frame_rarity')
    reliable = frame in ('N', 'R', 'SR', 'SSR') and item.get('rarity_confidence', 0) >= .55
    if not reliable or frame == 'N':
        return (3, 0)
    if blacklist:
        return (2, 0)
    if frame == 'R':
        return (1, 0)
    if change and replacement:
        return (0, int(frame == 'SR'))
    return (-1, 0)


def deck_view_complete(image, boxes, *, change=False):
    """Recognize a whole top-aligned grid, not merely fewer than 20 detections.

    A contiguous short last row proves the end only at the observed top and
    without a scrollbar indicating unseen content. Holes/partial rows fail.
    Delete and exchange have different pane tops and capacities.
    """
    top, capacity = (620, 3) if change else (380, 5)
    if not boxes or image is None:
        return False
    thumb = deck_scrollbar_position(image, top=top)
    if thumb is not None and (thumb[0] > top + 40 or thumb[1] < 1080):
        return False
    rows = []
    for box in sorted(boxes, key=lambda b: (b[1], b[0])):
        x, y, w, h = box
        if not (90 <= w <= 145 and 90 <= h <= 145 and y+h <= (1100 if change else 1115)):
            return False
        row = next((r for r in rows if abs(np.median([b[1] for b in r])-y) <= 10), None)
        if row is None:
            rows.append([box])
        else:
            row.append(box)
    if not 2 <= len(rows) <= capacity or not top <= min(b[1] for b in rows[0]) <= top+25:
        return False
    ys = [float(np.median([b[1] for b in row])) for row in rows]
    if any(not 136 <= b-a <= 156 for a, b in zip(ys, ys[1:])):
        return False
    for index, row in enumerate(rows):
        columns = sorted(round((b[0]-82)/146) for b in row)
        if columns != list(range(len(row))) or len(row) > 4:
            return False
        if any(abs(b[0]-(82+146*round((b[0]-82)/146))) > 10 for b in row):
            return False
        if index < len(rows)-1 and len(row) != 4:
            return False
    if len(rows[-1]) == 4:
        return thumb is not None  # Full rows need positive scrollbar evidence.
    # Re-run edge completion: missing detections must not masquerade as the
    # short final row. No geometry-only boxes are invented by this operation.
    return len(complete_grid_rows(image, boxes, top=top, bottom=1100 if change else 1115)) == len(boxes)


def scan_identity_known(item):
    """Accept strong, unambiguous art + frame evidence for ranking, not submission.

    Confidence is the existing template score, not a calibrated probability.
    Keep OCR verification separate; submit must still read the selected name.
    """
    if item.get('probe_failed'):
        return False
    if item.get('identity_verified'):
        return True
    if item.get('identity_conflict'):
        return False
    if (item.get('square_art_trusted') and item.get('name_key')
            and item.get('square_art_score', 0) >= .56
            and item.get('square_art_margin', 0) >= .12
            and item.get('rarity') in ('N', 'R', 'SR', 'SSR')):
        return True
    if item.get('square_art_attempted'):
        # Rejection cannot be undone by the older, lower-precision gallery.
        return False
    return bool(item.get('name_key')
                and item.get('name_confidence', 0) >= DECK_IDENTITY_MIN_CONFIDENCE
                and item.get('name_score', 0) >= .375
                and item.get('name_margin', 0) >= .12
                and item.get('rarity') in ('N', 'R', 'SR', 'SSR')
                and item.get('frame_rarity') == item.get('rarity')
                and item.get('rarity_confidence', 0) >= .30)


def deck_scrollbar_position(image, *, top=620):
    """Return the narrow gray thumb's vertical extent, or no reliable result."""
    if image is None or image.ndim != 3 or image.shape[0] < 1105 or image.shape[1] < 695:
        return None
    region_top = top + 10
    pixels = image[region_top:1105, 675:695, :3].astype(np.float32)
    gray = ((pixels.max(axis=2) - pixels.min(axis=2) < 40)
            & (pixels.mean(axis=2) >= 100) & (pixels.mean(axis=2) <= 235))
    runs = []
    for column in range(gray.shape[1]):
        changes = np.diff(np.r_[False, gray[:, column], False].astype(np.int8))
        for start, end in zip(np.flatnonzero(changes == 1), np.flatnonzero(changes == -1)):
            if end - start >= 100:
                runs.append((column, int(start), int(end)))
    groups = []
    for run in runs:
        group = next((group for group in groups if group[-1][0] == run[0] - 1
                      and abs(group[-1][1] - run[1]) <= 8
                      and abs(group[-1][2] - run[2]) <= 8), None)
        if group is None:
            groups.append([run])
        else:
            group.append(run)
    thumbs = [group for group in groups if 2 <= len(group) <= 10]
    if len(thumbs) != 1:
        return None
    return (region_top + min(run[1] for run in thumbs[0]),
            region_top + max(run[2] for run in thumbs[0]))


def deck_scrollbar_at_top(image, *, top=620):
    """Absence/ambiguity is not proof that the deck is at the top."""
    position = deck_scrollbar_position(image, top=top)
    return position[0] <= top + 40 if position is not None else None


def same_deck_view(first, second, first_boxes, second_boxes, *, top=620):
    """Compare stationary card centres/art, excluding selection and background.

    This is only viewport movement evidence, not a card identity matcher. Every
    visible tile needs a unique spatial pair and unchanged interior artwork;
    an observed scrollbar movement also vetoes an apparently identical deck.
    """
    if first.shape != second.shape or not first_boxes or not second_boxes or len(first_boxes) != len(second_boxes):
        return False
    first_scroll = deck_scrollbar_position(first, top=top)
    second_scroll = deck_scrollbar_position(second, top=top)
    if first_scroll is not None and second_scroll is not None and max(
            abs(a-b) for a, b in zip(first_scroll, second_scroll)) > 8:
        return False
    from .removal import selected_card_box_matches
    remaining = list(second_boxes)
    compared = 0
    for old in first_boxes:
        x, y, width, height = map(int, old)
        cx, cy = x + width/2, y + height/2
        matches = [index for index, (nx, ny, nw, nh) in enumerate(remaining)
                   if max(abs(nx+nw/2-cx), abs(ny+nh/2-cy)) <= 6
                   and abs(nw-width) <= 8 and abs(nh-height) <= 24]
        if len(matches) != 1:
            return False
        new = remaining.pop(matches[0])
        nx, ny, nw, nh = new
        # Selection can tint the artwork as well as add orange brackets.
        # Keep its geometry requirement, but use the other cards as visual
        # anchors. Never infer no movement from selected cards alone.
        if selected_card_box_matches(first, old) or selected_card_box_matches(second, new):
            continue
        ncx, ncy = round(nx+nw/2), round(ny+nh/2)
        cx, cy = round(cx), round(cy)
        # The central half avoids the outer orange brackets, rarity frame,
        # and most edge icons. Align detector jitter without permitting a
        # card-sized translation that would hide a real scroll.
        radius = round(min(width, nw) * .25)
        if radius < 20 or min(cx-radius, cy-radius, ncx-radius-4, ncy-radius-4) < 0:
            return False
        if max(cx+radius, ncx+radius+4) > first.shape[1] or max(cy+radius, ncy+radius+4) > first.shape[0]:
            return False
        sample = first[cy-radius:cy+radius, cx-radius:cx+radius, :3].astype(np.int16)
        matched = False
        for dy in range(-4, 5):
            for dx in range(-4, 5):
                current = second[ncy+dy-radius:ncy+dy+radius, ncx+dx-radius:ncx+dx+radius, :3].astype(np.int16)
                difference = np.abs(sample-current)
                if difference.mean() <= 12 and np.quantile(difference, .95) <= 40:
                    matched = True
                    break
            if matched:
                break
        if not matched:
            return False
        compared += 1
    return compared > 0


def normalize_deck_boxes(image, boxes, *, top=380, bottom=None):
    """Fit detector tiles to physical square borders before geometry filtering.

    Shop prices behind the transparent deletion list sometimes extend a YOLO
    box downwards. A highlighted tile can also extend above the pane boundary.
    Neither is evidence that the card itself is partial. Use peer widths and
    four actual straight edges to repair these boxes, never clamp blindly.
    """
    bottom = (1115 if top == 380 else 1100) if bottom is None else bottom
    peers = [box[2] for box in boxes if 50 <= box[0] <= 550
             and top-16 <= box[1] < 1100 and 100 <= box[2] <= 126
             and 90 <= box[3] <= 160]
    size = float(np.median(peers)) if len(peers) >= 2 else None
    pixels = image[:, :, :3].astype(np.float32)

    def edge_pair(expected, profiles):
        options = [(position, score) for position, score in profiles if score >= .65]
        pairs = [(left, right) for left, _ in options for right, _ in options
                 if size-7 <= right-left <= size+7
                 and abs(left-expected) <= 16 and abs(right-expected-size) <= 20]
        if not pairs:
            return None
        return min(pairs, key=lambda pair: (abs(pair[0]-expected)
                   + abs(pair[1]-pair[0]-size)*1.5, pair[0]))

    normalized = []
    for original in boxes:
        box = list(map(int, original))
        x, y, width, height = box
        if (size is not None and 50 <= x <= 550 and top-16 <= y < 1100
                and 90 <= width <= 150 and 90 <= height <= 170
                and (abs(width-height) > 8 or y < top or height > 145)):
            # Oversized highlights are centred on the actual card. Excess
            # height from the shop price strip is below the actual top edge.
            expected_x = round(x+(width-size)/2) if width > size+8 else x
            expected_y = y
            left, right = expected_x-16, round(expected_x+size+20)
            upper, lower = expected_y-16, round(expected_y+size+20)
            if left >= 1 and upper >= 1 and right < image.shape[1] and lower < image.shape[0]:
                inset = 22
                vertical = pixels[expected_y+inset:round(expected_y+size-inset), left-1:right]
                horizontal = pixels[upper-1:lower, expected_x+inset:round(expected_x+size-inset)]
                vx = (np.max(np.abs(np.diff(vertical, axis=1)), axis=2) > 22).mean(axis=0)
                hy = (np.max(np.abs(np.diff(horizontal, axis=0)), axis=2) > 22).mean(axis=1)
                xpair = edge_pair(expected_x, list(zip(range(left, right), vx)))
                ypair = edge_pair(expected_y, list(zip(range(upper, lower), hy)))
                if xpair and ypair:
                    bx, ex = xpair
                    by, ey = ypair
                    if abs((ex-bx)-(ey-by)) <= 8:
                        box = [bx, by, ex-bx, ey-by]
        x, y, width, height = box
        if (top <= y and y+height <= bottom and 50 <= x and x+width <= 675
                and 90 <= width <= 145 and 90 <= height <= 145
                and .80 <= width/height <= 1.25
                and not any(intersection_over_union(box, old) >= .45 for old in normalized)):
            normalized.append(box)
    return complete_grid_rows(image, normalized, top=top, bottom=bottom)


def complete_grid_rows(image, boxes, *, top=380, bottom=1100):
    """Recover missed deck tiles using an anchored grid and four real edges.

    Two coherent rows also locate an entirely missed row, including a single
    last tile. Grid positions are search windows, never evidence of a card.
    Partial tiles outside the pane and slots without four edges are rejected.
    """
    result = [list(box) for box in boxes]
    rows = []
    for box in sorted(boxes, key=lambda b: b[1]):
        row = next((row for row in rows if abs(np.median([b[1] for b in row]) - box[1]) <= 8), None)
        if row is None:
            rows.append([box])
        else:
            row.append(box)
    pixels = image[:, :, :3].astype(np.float32)
    supported = [row for row in rows if len(row) >= 2]
    search_rows = list(supported)
    # Establish both column and row spacing from this frame. This can recover
    # a model-missed final single-card row without inventing four new cards.
    spacings = []
    for row in supported:
        xs = sorted(b[0] for b in row)
        for left, right in zip(xs, xs[1:]):
            steps = round((right-left)/146)
            if 1 <= steps <= 3 and abs((right-left)/steps-146) <= 6:
                spacings.append((right-left)/steps)
    if len(supported) >= 2 and spacings:
        pitch = float(np.median(spacings))
        common_height = float(np.median([b[3] for row in supported for b in row]))
        # Detector top edges shift when a highlight makes a box taller. Row
        # centres keep that error from accumulating into the next missing row.
        ys = sorted(float(np.median([b[1]+b[3]/2 for b in row]))-common_height/2
                    for row in supported)
        gaps = [(b-a)/round((b-a)/pitch) for a, b in zip(ys, ys[1:])
                if round((b-a)/pitch) >= 1]
        if gaps and all(abs(gap-pitch) <= 6 for gap in gaps):
            row_pitch = float(np.median(gaps))
            anchors = [b for row in supported for b in row]
            width = round(float(np.median([b[2] for b in anchors])))
            height = round(float(np.median([b[3] for b in anchors])))
            origins = [b[0]-round((b[0]-82)/pitch)*pitch for b in anchors]
            origin = round(float(np.median(origins)))
            if max(abs(v-origin) for v in origins) <= 6 and abs(width-height) <= 10:
                y = ys[0]-row_pitch
                while y <= ys[-1]+row_pitch+1:
                    if not any(abs(y-old) <= 8 for old in ys):
                        search_rows.append([[origin, round(y), width, height],
                                            [round(origin+pitch), round(y), width, height]])
                    y += row_pitch
    for row in search_rows:
        if len(row) < 2:
            continue
        width = int(np.median([b[2] for b in row]))
        height = int(np.median([b[3] for b in row]))
        if abs(width - height) > 10:
            continue
        y = int(np.median([b[1] for b in row]))
        origin = int(np.median([b[0] - round((b[0] - 82) / 146) * 146 for b in row]))
        for column in range(4):
            x = origin + column * 146
            box = [x, y, width, height]
            if any(intersection_over_union(box, old) > .45 for old in result):
                continue
            if x < 50 or x + width > min(675, image.shape[1]-6) or y < top or y + height > min(bottom, image.shape[0]-6):
                continue
            # Search a narrow band for the rounded frame's straight edges.
            edges = []
            for edge_x in (x, x + width):
                strip = pixels[y + 18:y + height - 18, edge_x - 5:edge_x + 6]
                # A straight edge must occupy one line, not merely contain
                # scattered texture somewhere across the entire search band.
                edges.append(float((np.max(np.abs(np.diff(strip, axis=1)), axis=2) > 22).mean(axis=0).max()))
            for edge_y in (y, y + height):
                strip = pixels[edge_y - 5:edge_y + 6, x + 18:x + width - 18]
                edges.append(float((np.max(np.abs(np.diff(strip, axis=0)), axis=2) > 22).mean(axis=1).max()))
            if min(edges) >= .65:
                result.append(box)
    return sorted(result, key=lambda b: (b[1], b[0]))


class DeckIdentityCache:
    """Only exact OCR identities from this removal operation may be learned.

    Full inner-tile comparison retains the effect/cost overlays. Same artwork
    alone is not enough. Ambiguous fingerprints deliberately miss the cache.
    Final deletion/change confirmation remains mandatory on every cache hit.
    """
    def __init__(self):
        self.entries = []

    @staticmethod
    def _close(first, second):
        difference = np.abs(first - second)
        return difference.mean() <= .018 and np.quantile(difference, .95) <= .065

    def _matches(self, image, box):
        # Detector edges jitter independently of the physical card. Re-align
        # at the original crop size rather than loosening the identity score.
        x, y, w, h = map(int, box)
        samples = {}
        matches = []
        for old, identity, (ow, oh) in self.entries:
            if abs(ow - w) > 6 or abs(oh - h) > 6:
                continue
            size = (ow, oh)
            if size not in samples:
                cx, cy = round(x + (w - ow) / 2), round(y + (h - oh) / 2)
                samples[size] = [self.fingerprint(image, [cx + dx, cy + dy, ow, oh])
                                 for dx in range(-3, 4) for dy in range(-3, 4)]
            if any(sample is not None and self._close(old, sample) for sample in samples[size]):
                matches.append(identity)
        return matches

    @staticmethod
    def fingerprint(image, box):
        x, y, w, h = map(int, box)
        crop = image[y + 7:y + h - 7, x + 7:x + w - 7]
        sample = _resize_rgb_bilinear(crop, 48)
        # Colour differences between channels are not image detail: a flat
        # blue panel must not become a reusable card fingerprint.
        return sample if sample.size and sample.std(axis=(0, 1)).mean() > .04 else None

    def restore(self, image, items):
        restored = []
        for item in items:
            copy = dict(item, detail_selected=False)
            matches = self._matches(image, item['box'])
            keys = {(m['name_key'], m['rarity']) for m in matches}
            conflict = (len(keys) == 1 and item.get('name_key')
                        and item.get('name_confidence', 0) >= DECK_IDENTITY_MIN_CONFIDENCE
                        and item['name_key'] != matches[0]['name_key'])
            if len(keys) == 1 and not conflict:
                copy.update(matches[0], probe_failed=False)
            restored.append(copy)
        return restored

    def remember(self, image, items):
        for item in items:
            if not item.get('identity_verified') or item.get('probe_failed'):
                continue
            sample = self.fingerprint(image, item['box'])
            if sample is None:
                continue
            identity = {key: item[key] for key in ('name', 'name_key', 'rarity', 'name_confidence', 'identity_verified')}
            identity['rarity_confidence'] = 1.
            if not any(old_identity == identity and self._close(old, sample)
                       for old, old_identity, _ in self.entries):
                self.entries.append((sample, identity, tuple(item['box'][2:])))
