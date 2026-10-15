"""HTTPS proof using stdlib only; never print response bodies, credentials or exceptions."""
import base64
import hashlib
import hmac
import json
import logging
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
import urllib.parse
import uuid

logging.disable(logging.CRITICAL)

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

def main():
    host = os.environ['DOMAIN']
    local = os.getenv('DEPLOY_LOCAL_TLS') == '1'
    if local and host != 'localhost':
        raise ValueError()
    context = ssl._create_unverified_context() if local else ssl.create_default_context()
    # Ignore ambient HTTP(S)_PROXY so credentials go straight to the selected host.
    client = urllib.request.build_opener(urllib.request.ProxyHandler({}),
        urllib.request.HTTPSHandler(context=context), NoRedirect())
    base = 'https://' + host
    response_headers = {}

    def request(path, body=None, headers=None):
        nonlocal response_headers
        req = urllib.request.Request(base + path, data=body, headers=headers or {})
        try:
            with client.open(req, timeout=10) as response:
                response_headers = dict(response.headers)
                return response.status, response.read(1048576)
        except urllib.error.HTTPError as error:
            response_headers = dict(error.headers)
            return error.code, error.read(1048576)

    for attempt in range(60):
        try:
            code, data = request('/health')
            if code == 200 and json.loads(data).get('status') == 'ok':
                break
        except (OSError, ValueError):
            pass
        time.sleep(2)
    else:
        raise ValueError('health')
    print('PASS: HTTPS responds and /health is OK', flush=True)
    path = '/webhooks/shopify/orders-create'
    headers = {'Content-Type': 'application/json', 'X-Shopify-Webhook-Id': str(uuid.uuid4()),
               'X-Shopify-Topic': 'orders/create', 'X-Shopify-Hmac-Sha256': 'invalid'}
    assert request(path, b'{}', headers)[0] == 401
    print('PASS: bad Shopify signature refused', flush=True)
    for admin_path in ('/admin', '/admin/login', '/admin/orders', '/admin-extra'):
        assert request(admin_path)[0] == 401
    print('PASS: /admin* refuses requests without basic auth', flush=True)
    basic = base64.b64encode((os.environ['CADDY_BASIC_AUTH_USER'] + ':' +
                             os.environ['CADDY_BASIC_AUTH_PASSWORD']).encode()).decode()
    assert request('/admin/login', headers={'Authorization': 'Basic ' + basic})[0] == 200
    assert request('/admin/orders', headers={'Authorization': 'Basic ' + basic})[0] == 303
    print('PASS: basic auth works and existing ADMIN_TOKEN login is still required', flush=True)
    login_headers = {'Authorization': 'Basic ' + basic, 'Content-Type': 'application/x-www-form-urlencoded'}
    login_body = urllib.parse.urlencode({'token': os.environ['ADMIN_TOKEN']}).encode()
    assert request('/admin/login', login_body, login_headers)[0] == 303
    cookie = next((v.lower() for k,v in response_headers.items() if k.lower() == 'set-cookie'), '')
    assert 'secure' in cookie and 'httponly' in cookie and 'samesite=strict' in cookie
    print('PASS: admin session cookie is Secure, HttpOnly and SameSite=Strict', flush=True)
    # Check the production API is protected before using an operator key.
    assert request('/orders/' + str(uuid.uuid4()))[0] == 401
    number = uuid.uuid4().int % 10**15
    payload = {'id': number, 'currency': 'PKR', 'email': 'smoke@example.com', 'test': True,
               'customer': {'first_name': 'Deployment', 'last_name': 'Smoke'},
               'line_items': [{'sku': 'DEPLOY-SMOKE', 'quantity': 1, 'price': '10.00'}]}
    # Same method as scripts/e2e_shopify_odoo.py: compact UTF-8 JSON; HMAC-SHA256;
    # base64 of the digest over EXACTLY the transmitted bytes.
    body = json.dumps(payload, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
    headers['X-Shopify-Hmac-Sha256'] = base64.b64encode(hmac.new(
        os.environ['SHOPIFY_WEBHOOK_SECRET'].encode('utf-8'), body, hashlib.sha256).digest()).decode()
    headers['X-Shopify-Webhook-Id'] = str(uuid.uuid4())
    code, data = request(path, body, headers)
    assert code == 200
    order_id = str(uuid.UUID(json.loads(data)['order_id']))
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        code, data = request('/orders/' + order_id, headers={'X-API-Key': os.environ['DEPLOY_OPERATOR_API_KEY']})
        assert code == 200
        state = json.loads(data)['status']
        if state == 'CONFIRMED':
            print('PASS: signed sample Shopify order reached CONFIRMED', flush=True)
            return
        if state in {'REJECTED', 'FAILED_DEAD', 'CANCELLED', 'PENDING_APPROVAL'}:
            break
        time.sleep(1)
    raise ValueError('confirmation')

if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('FAIL: deployment smoke test; credentials and response bodies hidden.', file=sys.stderr)
        sys.exit(1)
