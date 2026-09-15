from downloader.krx18_wp_public_sources import extract_server_targets, post_id_from_url


def test_post_id_from_krx18_url():
    assert post_id_from_url('https://krx18.com/movies/84170-femdom-deadly-thigh-squeeze-her-absolute-leg-scissors/') == '84170'


def test_extract_only_explicit_server_targets():
    html = '<div><span>Video Sources</span><a href="https://playkrx18.site/x">Server 1</a><a href="https://mov18plus.cloud/y">Server 2</a><a href="https://ads.example/">Advertisement</a></div>'
    targets = set(extract_server_targets(html, 'https://krx18.com/movies/84170-test/'))
    assert targets == {'https://playkrx18.site/x', 'https://mov18plus.cloud/y'}
