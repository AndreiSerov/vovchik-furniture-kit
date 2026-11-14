from kitchen.cutting import Placement, SheetCut, cut_order
from kitchen.cutting_pdf import _leader, _leader_path, _pad, scheme_labels, scheme_scale
from kitchen.pdf import page_area


def test_dense_sheet_keeps_cut_numbers_inside_diagram():
    placements = [
        Placement(part=f"Н{i}-царга", x=10 + i * 24, y=10, w=20, h=100, rotated=False) for i in range(20)
    ]
    cuts = cut_order(placements, (2800, 2070), 4, 10)
    sheet = SheetCut(number=1, placements=placements, cuts=cuts, parts_area_m2=0.04, utilization=0.01)
    k = scheme_scale(page_area().w, (2800, 2070))
    _, boxes = scheme_labels((2800, 2070), sheet, 4, k)
    assert len(boxes) == len(cuts)
    for i, (_, a) in enumerate(boxes):
        for _, b in boxes[i + 1 :]:
            assert a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1]
    by_number = {c.number: c for c in cuts}
    displaced = 0
    for number, box in boxes:
        x0, y0, x1, y1 = box
        assert 0 <= x0 < x1 <= 2800 * k
        assert 0 <= y0 < y1 <= 2070 * k
        cut = by_number[number]
        leader = _leader(cut, box, 4, k)
        if leader is not None:
            displaced += 1
            _, _, x, y = leader
            if cut.direction == "vertical":
                assert x == (cut.position + 2) * k and cut.start * k <= y <= cut.end * k
            else:
                assert y == (cut.position + 2) * k and cut.start * k <= x <= cut.end * k
    assert displaced > 0  # плотная раскладка требует выносок


def test_dense_leaders_remain_visible_through_other_labels():
    placements = [
        Placement(part=f"Н{i}-царга", x=10 + i * 24, y=10, w=20, h=100, rotated=False) for i in range(20)
    ]
    cuts = cut_order(placements, (2800, 2070), 4, 10)
    sheet = SheetCut(number=1, placements=placements, cuts=cuts, parts_area_m2=0.04, utilization=0.01)
    k = scheme_scale(page_area().w, (2800, 2070))
    parts, boxes = scheme_labels((2800, 2070), sheet, 4, k)
    for cut, (number, box) in zip(cuts, boxes, strict=True):
        assert cut.number == number
        obstacles = [other for n, other in boxes if n != number] + [part.box for part in parts]
        path = _leader_path(cut, box, 4, k, obstacles, (2800 * k, 2070 * k))
        if _leader(cut, box, 4, k) is None:
            assert path is None
            continue
        assert path is not None
        x, y = path[-1]
        if cut.direction == "vertical":
            assert x == (cut.position + 2) * k and cut.start * k <= y <= cut.end * k
        else:
            assert y == (cut.position + 2) * k and cut.start * k <= x <= cut.end * k
        for (x0, y0), (x1, y1) in itertools.pairwise(path):
            assert 0 <= x0 <= 2800 * k and 0 <= y0 <= 2070 * k
            for left, top, right, bottom in (_pad(obstacle, 0.7) for obstacle in obstacles):
                if x0 == x1:
                    assert not (left < x0 < right and max(min(y0, y1), top) < min(max(y0, y1), bottom))
                else:
                    assert y0 == y1
                    assert not (top < y0 < bottom and max(min(x0, x1), left) < min(max(x0, x1), right))


import itertools
