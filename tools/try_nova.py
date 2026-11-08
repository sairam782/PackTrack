"""Send one small Nova 2 Lite request using a Bedrock API key.

Run: .venv/bin/python tools/try_nova.py --region us-east-1
Uses AWS_BEARER_TOKEN_BEDROCK or prompts without echoing/storing the key.
Reference: https://docs.aws.amazon.com/nova/latest/nova2-userguide/using-converse-api.html
"""
import argparse
import getpass
import os
import re
import sys

import requests


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--region', default=os.getenv('AWS_REGION') or os.getenv('AWS_DEFAULT_REGION') or 'us-east-1')
    parser.add_argument('--model', default='us.amazon.nova-2-lite-v1:0', help='Bedrock model or inference profile ID; match your AWS region')
    args = parser.parse_args()
    if not re.fullmatch(r'[a-z]{2}-[a-z]+-\d+', args.region):
        parser.error('Use an AWS region such as us-east-1')
    key = os.getenv('AWS_BEARER_TOKEN_BEDROCK', '').strip()
    if not key:
        if not sys.stdin.isatty():
            print('No key configured. Run in your terminal for a hidden key prompt, or set AWS_BEARER_TOKEN_BEDROCK.', file=sys.stderr)
            return 2
        try:
            key = getpass.getpass('Bedrock API key (hidden): ').strip()
        except (EOFError, KeyboardInterrupt):
            return 2
    if not key:
        print('No key entered; no request sent.', file=sys.stderr)
        return 2
    from urllib.parse import quote
    url = f'https://bedrock-runtime.{args.region}.amazonaws.com/model/{quote(args.model, safe="")}/converse'
    print(f'Testing {args.model} in {args.region}…')
    try:
        response = requests.post(
            url,
            headers={'Authorization': f'Bearer {key}'},
            json={
                'messages': [{'role': 'user', 'content': [{'text': 'Reply with exactly: PackTrack Nova connection works.'}]}],
                'inferenceConfig': {'maxTokens': 64},
            },
            timeout=(10, 60),
            allow_redirects=False,
        )
    except requests.RequestException:
        print('Connection failed. Check network access and AWS region.', file=sys.stderr)
        return 1
    if response.status_code != 200:
        print(f'Bedrock returned HTTP {response.status_code}. Check key expiry, model access, region, and inference profile.', file=sys.stderr)
        return 1
    try:
        result = response.json()
        reply = '\n'.join(item['text'] for item in result['output']['message']['content'] if 'text' in item)
        if not reply:
            raise ValueError('Missing text response')
    except (ValueError, KeyError, TypeError):
        print('Bedrock responded, but no readable model reply was returned.', file=sys.stderr)
        return 1
    print(reply.replace(key, '[REDACTED]'))
    print('SUCCESS: received a live Nova response.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
