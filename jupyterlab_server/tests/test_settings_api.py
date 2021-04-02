"""Test the Settings service API.
"""

import pytest
import json
import json5
import tornado

from strict_rfc3339 import rfc3339_to_timestamp

from .utils import expected_http_error
from .utils import maybe_patch_ioloop, big_unicode_string


from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import parse_qs, urlparse, urljoin

from openapi_core.validation.request.datatypes import (
    RequestParameters, OpenAPIRequest
)
from openapi_core.validation.response.datatypes import OpenAPIResponse
from openapi_core import create_spec
from openapi_core.validation.request.validators import RequestValidator
from openapi_core.validation.response.validators import ResponseValidator
from ruamel.yaml import YAML


def wrap_request(request, spec):
    """Wrap a tornado request as an open api request"""
    # Extract cookie dict from cookie header
    cookie = SimpleCookie()
    cookie.load(request.headers.get('Set-Cookie', ''))
    cookies = {}
    for key, morsel in cookie.items():
        cookies[key] = morsel.value

    # extract the path
    o = urlparse(request.url)

    # extract the best matching url
    # work around lack of support for path parameters which can contain slashes
    # https://github.com/OAI/OpenAPI-Specification/issues/892
    url = None
    for path in spec.paths:
        if url:
            continue
        has_arg = '{' in path
        if has_arg:
            path = path[:path.index('{')]
        if path in o.path:
            u = o.path[o.path.index(path):]
            if not has_arg and len(u) == len(path):
                url = u
            if has_arg:
                url = u[:len(path)] + r'foo/'
    if url is None:
        raise ValueError(f'Could not find matching pattern for {o.path}')

    # gets deduced by path finder against spec
    path = {}

    # Order matters because all tornado requests
    # include Accept */* which does not necessarily match the content type
    mimetype = request.headers.get('Content-Type') or \
        request.headers.get('Accept') or 'application/json'

    parameters = RequestParameters(
        query=parse_qs(o.query),
        header=dict(request.headers),
        cookie=cookies,
        path=path,
    )

    return OpenAPIRequest(
        full_url_pattern=url,
        method=request.method.lower(),
        parameters=parameters,
        body=request.body,
        mimetype=mimetype,
    )


def wrap_response(response):
    """Wrap a tornado response as an open api response"""
    mimetype = response.headers.get('Content-Type') or 'application/json'
    return OpenAPIResponse(
        data=response.body,
        status_code=response.code,
        mimetype=mimetype,
    )


def validate_request(response):
    """Validate an API request"""
    path = (Path(__file__) / '../../../docs/rest-api.yml').resolve()
    yaml = YAML(typ='safe')
    spec_dict = yaml.load(path.read_text(encoding='utf-8'))
    spec = create_spec(spec_dict)

    validator = RequestValidator(spec)
    request = wrap_request(response.request, spec)
    result = validator.validate(request)
    print(result.errors)
    result.raise_for_errors()

    validator = ResponseValidator(spec)
    response = wrap_response(response)
    result = validator.validate(request, response)
    print(result.errors)
    result.raise_for_errors()


async def test_get_settings(jp_fetch, labserverapp):
    id = '@jupyterlab/apputils-extension:themes'
    r = await jp_fetch('lab', 'api', 'settings', id)
    validate_request(r)
    res = r.body.decode()
    data = json.loads(res)
    assert data['id'] == id
    schema = data['schema']
    # Check that overrides.json file is respected.
    assert schema['properties']['theme']['default'] == 'JupyterLab Dark'
    assert 'raw' in res


async def test_get_federated(jp_fetch, labserverapp):
    id = '@jupyterlab/apputils-extension-federated:themes'
    r = await jp_fetch('lab', 'api', 'settings', id)
    validate_request(r)
    res = r.body.decode()
    assert 'raw' in res


async def test_get_bad(jp_fetch, labserverapp):
    with pytest.raises(tornado.httpclient.HTTPClientError) as e:
        await jp_fetch('foo')
    assert expected_http_error(e, 404)

async def test_listing(jp_fetch, labserverapp):
    ids = [
        '@jupyterlab/apputils-extension:themes',
        '@jupyterlab/apputils-extension-federated:themes',
        '@jupyterlab/codemirror-extension:commands',
        '@jupyterlab/codemirror-extension-federated:commands',
        '@jupyterlab/shortcuts-extension:plugin',
        '@jupyterlab/translation-extension:plugin',
        '@jupyterlab/unicode-extension:plugin'
    ]
    versions = ['N/A', 'N/A', 'test-version']
    r = await jp_fetch('lab', 'api', 'settings/')
    validate_request(r)
    res = r.body.decode()
    response = json.loads(res)
    response_ids = [item['id'] for item in response['settings']]
    response_schemas = [item['schema'] for item in response['settings']]
    response_versions = [item['version'] for item in response['settings']]
    assert set(response_ids) == set(ids)
    assert all(response_schemas)
    assert set(response_versions) == set(versions)
    last_modifieds = [item['last_modified'] for item in response['settings']]
    createds = [item['created'] for item in response['settings']]
    assert {None} == set(last_modifieds + createds)


async def test_patch(jp_fetch, labserverapp):
    id = '@jupyterlab/shortcuts-extension:plugin'

    r = await jp_fetch('lab', 'api', 'settings', id,
        method='PUT',
        body=json.dumps(dict(raw=json5.dumps(dict()))))
    validate_request(r)

    r = await jp_fetch('lab', 'api', 'settings', id,
        method='GET',
        )
    validate_request(r)
    data = json.loads(r.body.decode())
    first_created = rfc3339_to_timestamp(data['created'])
    first_modified = rfc3339_to_timestamp(data['last_modified'])

    r = await jp_fetch('lab', 'api', 'settings', id,
        method='PUT',
        body=json.dumps(dict(raw=json5.dumps(dict())))
        )
    validate_request(r)

    r = await jp_fetch('lab', 'api', 'settings', id,
        method='GET',
        )
    validate_request(r)
    data = json.loads(r.body.decode())
    second_created = rfc3339_to_timestamp(data['created'])
    second_modified = rfc3339_to_timestamp(data['last_modified'])

    assert first_created <= second_created
    assert first_modified < second_modified

    r = await jp_fetch('lab', 'api', 'settings/',
        method='GET',
        )
    validate_request(r)
    data = json.loads(r.body.decode())
    listing = data['settings']
    list_data = [item for item in listing if item['id'] == id][0]
    # TODO(@echarles) Check this...
#    assert list_data['created'] == data['created']
#    assert list_data['last_modified'] == data['last_modified']


async def test_patch_unicode(jp_fetch, labserverapp):
    id = '@jupyterlab/unicode-extension:plugin'
    settings = dict(comment=big_unicode_string[::-1])
    payload = dict(raw=json5.dumps(settings))

    r = await jp_fetch('lab', 'api', 'settings', id,
        method='PUT',
        body=json.dumps(payload)
        )
    validate_request(r)

    r = await jp_fetch('lab', 'api', 'settings', id,
        method='GET',
        )
    validate_request(r)
    data = json.loads(r.body.decode())
    assert data["settings"]["comment"] == big_unicode_string[::-1]

async def test_patch_wrong_id(jp_fetch, labserverapp):
    with pytest.raises(tornado.httpclient.HTTPClientError) as e:
        await jp_fetch('foo',
            method='PUT',
            body=json.dumps(dict(raw=json5.dumps(dict())))
        )
    assert expected_http_error(e, 404)

async def test_patch_bad_data(jp_fetch, labserverapp):
    with pytest.raises(tornado.httpclient.HTTPClientError) as e:
        settings = dict(keyMap=10)
        payload = dict(raw=json5.dumps(settings))
        await jp_fetch('foo',
            method='PUT',
            body=json.dumps(payload)
        )
    assert expected_http_error(e, 404)

async def test_patch_invalid_payload_format(jp_fetch, labserverapp):
    id = '@jupyterlab/apputils-extension:themes'

    with pytest.raises(tornado.httpclient.HTTPClientError) as e:
        settings = dict(keyMap=10)
        payload = dict(foo=json5.dumps(settings))
        await jp_fetch('lab', 'api', 'settings', id,
            method='PUT',
            body=json.dumps(payload)
        )
    assert expected_http_error(e, 400)

async def test_patch_invalid_json(jp_fetch, labserverapp):
    id = '@jupyterlab/apputils-extension:themes'

    with pytest.raises(tornado.httpclient.HTTPClientError) as e:
        payload_str = 'eh'
        await jp_fetch('lab', 'api', 'settings', id,
            method='PUT',
            body=json.dumps(payload_str)
        )
    assert expected_http_error(e, 400)
