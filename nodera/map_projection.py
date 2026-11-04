"""Equirectangular viewport fit with antimeridian-aware route bounds."""


def project_view(locations, width, height, top=0, bottom=0, zoom=False):
    frame = (12, top + 4, max(40, width - 24), max(40, height - top - bottom - 8))
    base = min(frame[2] / 360, frame[3] / 145)
    center_x, center_y, scale = 180, 72.5, base
    if locations and zoom:
        xs = sorted((item["lon"] + 180) % 360 for item in locations)
        gaps = [
            (xs[(index + 1) % len(xs)] + (360 if index == len(xs) - 1 else 0) - value, index)
            for index, value in enumerate(xs)
        ]
        _, cut = max(gaps)
        start = xs[(cut + 1) % len(xs)]
        unwrapped = [x + (360 if x < start else 0) for x in xs]
        ys = [85 - item["lat"] for item in locations]
        center_x = (min(unwrapped) + max(unwrapped)) / 2
        center_y = (min(ys) + max(ys)) / 2
        span_x, span_y = max(unwrapped) - min(unwrapped), max(ys) - min(ys)
        scale = min(
            (frame[2] - 56) / max(14, span_x * 1.45 + 8),
            (frame[3] - 56) / max(10, span_y * 1.45 + 8),
            base * 5,
        )
        scale = max(base * 0.5, scale)
    return {
        "frame": frame,
        "scale": scale,
        "center_x": center_x,
        "x": frame[0] + frame[2] / 2 - center_x * scale,
        "y": frame[1] + frame[3] / 2 - center_y * scale,
    }


def project_point(location, view):
    x = (location["lon"] + 180) % 360
    x += 360 * round((view["center_x"] - x) / 360)
    return view["x"] + x * view["scale"], view["y"] + (85 - location["lat"]) * view["scale"]


def route_links(locations, has_client=False):
    links = []
    start = 1 if has_client else 0
    for index in range(start, len(locations)):
        current = locations[index]
        if has_client and (
            (current.get("step") == 0 and current.get("order_known"))
            or (current.get("entry") and not current.get("order_known"))
        ):
            links.append((0, index))
        if index > start:
            previous = locations[index - 1]
            if (
                current.get("order_known")
                and current.get("route") == previous.get("route")
                and current.get("step", 0) == previous.get("step", 0) + 1
            ):
                links.append((index - 1, index))
    return links


def schematic_links(locations, has_client=False):
    """Join known points for display; these edges do not assert transport order."""
    confirmed = set(route_links(locations, has_client))
    by_route = {}
    for index in range(1 if has_client else 0, len(locations)):
        by_route.setdefault(locations[index].get("route", 0), []).append(index)
    result = []
    for indices in by_route.values():
        if not any(not locations[index].get("order_known", True) for index in indices):
            continue
        entry = next((index for index in indices if locations[index].get("entry")), indices[0])
        ordered = [entry, *[index for index in indices if index != entry]]
        if has_client and (0, entry) not in confirmed:
            result.append((0, entry))
        result.extend((a, b) for a, b in zip(ordered, ordered[1:]) if (a, b) not in confirmed)
    return result
