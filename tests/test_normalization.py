from dotdocs.normalization import normalize_plate, normalize_unit, normalize_vin


def test_normalizes_fleet_identifiers_for_matching():
    assert normalize_unit("Unit 097") == "97"
    assert normalize_unit(97) == "97"
    assert normalize_plate("v 32192") == "V32192"
    assert normalize_vin("1gb3-kzbk9-af141680") == "1GB3KZBK9AF141680"
