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
current_check = 'deployment environment'


def begin(name):
    global current_check
    current_check = name


def require(condition):
    if not condition:
        raise ValueError()


def passed():
    print('PASS: ' + current_check, flush=True)

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

    begin('HTTPS responds and /health is OK')
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
    passed()
    begin('bad Shopify signature refused')
    path = '/webhooks/shopify/orders-create'
    headers = {'Content-Type': 'application/json', 'X-Shopify-Webhook-Id': str(uuid.uuid4()),
               'X-Shopify-Topic': 'orders/create', 'X-Shopify-Hmac-Sha256': 'invalid'}
    require(request(path, b'{}', headers)[0] == 401)
    passed()
    begin('/admin* refuses missing Basic Auth credentials')
    for admin_path in ('/admin', '/admin/login', '/admin/orders', '/admin-extra'):
        require(request(admin_path)[0] == 401)
    passed()
    begin('/admin* refuses wrong Basic Auth credentials')
    wrong_password = base64.b64encode((os.environ['CADDY_BASIC_AUTH_USER'] + ':' +
                                      os.environ['CADDY_BASIC_AUTH_PASSWORD'] + '-wrong').encode()).decode()
    wrong_user = base64.b64encode((os.environ['CADDY_BASIC_AUTH_USER'] + '-wrong:' +
                                  os.environ['CADDY_BASIC_AUTH_PASSWORD']).encode()).decode()
    for admin_path in ('/admin', '/admin/login', '/admin/orders', '/admin-extra'):
        for wrong in (wrong_password, wrong_user):
            require(request(admin_path, headers={'Authorization': 'Basic ' + wrong})[0] == 401)
    passed()
    basic = base64.b64encode((os.environ['CADDY_BASIC_AUTH_USER'] + ':' +
                             os.environ['CADDY_BASIC_AUTH_PASSWORD']).encode()).decode()
    begin('valid Basic Auth reaches admin login')
    code, _ = request('/admin/login', headers={'Authorization': 'Basic ' + basic})
    if code in (301, 302, 303, 307, 308):
        location = next((v for k, v in response_headers.items() if k.lower() == 'location'), '')
        target = urllib.parse.urlsplit(urllib.parse.urljoin(base + '/admin/login', location))
        require(bool(location) and target.scheme == 'https' and target.netloc == host
                and (target.path == '/admin' or target.path.startswith('/admin/')))
        code, _ = request(target.path + ('?' + target.query if target.query else ''),
                          headers={'Authorization': 'Basic ' + basic})
    require(code == 200)
    passed()
    begin('ADMIN_TOKEN remains required behind valid Basic Auth')
    require(request('/admin/orders', headers={'Authorization': 'Basic ' + basic})[0] == 303)
    login_headers = {'Authorization': 'Basic ' + basic, 'Content-Type': 'application/x-www-form-urlencoded'}
    wrong_token = urllib.parse.urlencode({'token': os.environ['ADMIN_TOKEN'] + '-wrong'}).encode()
    require(request('/admin/login', wrong_token, login_headers)[0] == 401)
    passed()
    begin('ADMIN_TOKEN login succeeds behind Basic Auth')
    login_body = urllib.parse.urlencode({'token': os.environ['ADMIN_TOKEN']}).encode()
    require(request('/admin/login', login_body, login_headers)[0] == 303)
    cookie = next((v.lower() for k,v in response_headers.items() if k.lower() == 'set-cookie'), '')
    raw_cookie = next((v for k,v in response_headers.items() if k.lower() == 'set-cookie'), '')
    passed()
    begin('admin session cookie is Secure, HttpOnly and SameSite=Strict')
    require('secure' in cookie and 'httponly' in cookie and 'samesite=strict' in cookie)
    passed()
    begin('authenticated admin session reaches orders')
    require(request('/admin/orders', headers={'Authorization': 'Basic ' + basic,
                                              'Cookie': raw_cookie.split(';', 1)[0]})[0] == 200)
    passed()
    begin('order API refuses missing API key')
    # Check the production API is protected before using an operator key.
    require(request('/orders/' + str(uuid.uuid4()))[0] == 401)
    passed()
    begin('signed sample Shopify webhook is accepted')
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
    require(code == 200)
    order_id = str(uuid.UUID(json.loads(data)['order_id']))
    passed()
    begin('signed sample Shopify order reached CONFIRMED')
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        code, data = request('/orders/' + order_id, headers={'X-API-Key': os.environ['DEPLOY_OPERATOR_API_KEY']})
        require(code == 200)
        state = json.loads(data)['status']
        if state == 'CONFIRMED':
            passed()
            return
        if state in {'REJECTED', 'FAILED_DEAD', 'CANCELLED', 'PENDING_APPROVAL'}:
            break
        time.sleep(1)
    raise ValueError('confirmation')

if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('FAIL: ' + current_check + '; credentials and response bodies hidden.', file=sys.stderr)
        sys.exit(1)
