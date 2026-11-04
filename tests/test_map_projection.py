from nodera.map_projection import project_point, project_view, route_links, schematic_links


def test_auto_zoom_keeps_client_and_servers_visible():
    points = [
        {"lon": 37.62, "lat": 55.75},
        {"lon": 9.68, "lat": 50.96},
        {"lon": 27.28, "lat": 63.25},
    ]
    world = project_view(points, 800, 600, 48, 108)
    zoom = project_view(points, 800, 600, 48, 108, True)
    assert zoom["scale"] > world["scale"]
    x, y, width, height = zoom["frame"]
    for point in points:
        px, py = project_point(point, zoom)
        assert x <= px <= x + width and y <= py <= y + height


def test_dateline_points_use_short_geographic_extent():
    points = [{"lon": 179, "lat": 20}, {"lon": -179, "lat": 22}]
    zoom = project_view(points, 800, 600, zoom=True)
    left, right = [project_point(point, zoom)[0] for point in points]
    assert abs(left - right) < 100


def test_unknown_order_only_links_client_to_confirmed_entry():
    points = [
        {"role": "client"},
        {"role": "cdn", "route": 0, "step": 0, "order_known": False},
        {"role": "relay", "route": 0, "step": 1, "order_known": False, "entry": True},
    ]
    assert route_links(points, True) == [(0, 2)]
    points = [
        {"role": "client"},
        {"route": 0, "step": 0, "order_known": True},
        {"route": 0, "step": 1, "order_known": True},
    ]
    assert route_links(points, True) == [(0, 1), (1, 2)]
    points[2]["entry"] = True
    assert route_links(points, True) == [(0, 1), (1, 2)]


def test_unknown_route_draws_schematic_after_confirmed_entry():
    points = [
        {"role": "client"},
        {"route": 0, "step": 0, "order_known": False, "role": "cdn"},
        {"route": 0, "step": 1, "order_known": False, "entry": True},
    ]
    assert route_links(points, True) == [(0, 2)]
    assert schematic_links(points, True) == [(2, 1)]
