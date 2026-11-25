"""Read the owner badge beside the slot number on a 1920×1080 warehouse detail."""
import re
from functools import lru_cache


def _descriptor(image, mask):
    import cv2
    import numpy as np
    rgb = cv2.resize(image, (31, 31), interpolation=cv2.INTER_AREA)
    pixels = rgb[mask].astype('float32').ravel()
    pixels -= pixels.mean()
    edges = cv2.Canny(cv2.cvtColor(np.where(mask[:, :, None], rgb, 0), cv2.COLOR_RGB2GRAY), 50, 150)
    edges = edges.astype('float32').ravel()
    edges -= edges.mean()
    return tuple(vector / max(1, np.linalg.norm(vector)) for vector in (pixels, edges))


@lru_cache(maxsize=2)
def _portraits(matcher):
    import numpy as np
    y, x = np.ogrid[:31, :31]
    mask = (x-15)**2 + (y-15)**2 <= 12**2
    names, colors, edges = [], [], []
    for filename, image in matcher.icon_cache.items():
        match = re.fullmatch(r'IconRoleCircle([0-9]+)[.]webp', filename, re.I)
        if not match or image is None or image.ndim != 3 or image.shape[2] != 3:
            continue
        color, edge = _descriptor(image, mask)
        colors.append(color)
        edges.append(edge)
        names.append(matcher._find_agent_key_by_icon('IconRole'+match[1]))
    return mask, names, np.array(colors), np.array(edges)


def read_owner(detail, matcher):
    import cv2
    import numpy as np
    unknown = ('unknown', '')
    if detail is None or detail.shape != (501, 430, 3):
        return unknown
    badge = detail[54:85, 8:40]
    face = detail[54:85, 54:85]
    # The neighboring slot badge anchors the empty-background measurement.
    gray = cv2.cvtColor(badge, cv2.COLOR_RGB2GRAY)
    if gray.std() < 22 or np.percentile(gray, 95) < 75:
        return unknown
    low, high = np.percentile(face, [1, 99])
    beside = detail[54:85, 87:100]
    if (10 <= face.mean() <= 43 and high-low <= 18 and face.std() <= 5 and
            abs(float(face.mean())-float(beside.mean())) <= 4):
        return 'bag', ''
    if matcher is None:
        return unknown
    mask, names, colors, edges = _portraits(matcher)
    if len(names) < 2:
        return unknown
    # Resizing the game client can move the 31px portrait by one pixel.
    shifts = [(dy, dx) for dy in (-1, 0, 1) for dx in (-1, 0, 1)]
    observations = [_descriptor(detail[54+dy:85+dy, 54+dx:85+dx], mask) for dy, dx in shifts]
    rgb_by_shift = np.einsum('ij,kj->ik', colors, np.array([color for color, _ in observations]))
    edge_by_shift = np.einsum('ij,kj->ik', edges, np.array([edge for _, edge in observations]))
    rgb_scores, edge_scores = rgb_by_shift.max(axis=1), edge_by_shift.max(axis=1)
    best, second = rgb_scores.argsort()[-2:][::-1]
    if (best != edge_scores.argmax() or not names[best] or rgb_scores[best] < .82
            or rgb_scores[best]-rgb_scores[second] < .09 or edge_by_shift[best, rgb_by_shift[best].argmax()] < .58
            or edge_scores[best]-np.partition(edge_scores, -2)[-2] < .07):
        return unknown
    return 'worn', names[best]


def owner_label(disc):
    state = disc.get('ownerState')
    if state == 'bag':
        return '背包'
    if state == 'unknown':
        return '穿戴者待复核'
    return disc.get('location') or '穿戴状态未核实'
