#!/bin/bash
# Refresh AWS Bedrock credentials via Capitol-AI Grafana IMDS bridge.
# Output: .aws-session (env vars ready to paste into Claude Code)
#
# Usage:
#   bash refresh-aws.sh
#   source .aws-session       # load into current shell (optional)

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CAPITOL_DIR="$SCRIPT_DIR/src/open-source/capitol-ai-grafana"
OUTPUT_FILE="$SCRIPT_DIR/.aws-session"

echo "Refreshing AWS credentials via Grafana IMDS..."

# Find node binary (handles cases where node.exe is in path but node isn't, common in some Windows bash setups)
if command -v node >/dev/null 2>&1; then
  NODE_BIN="node"
elif command -v node.exe >/dev/null 2>&1; then
  NODE_BIN="node.exe"
else
  echo "Error: node (Node.js) is not found in your PATH."
  echo "Please install Node.js or ensure it is in your PATH."
  exit 1
fi

# Find python binary for parsing (usually more robust for JSON in this repo)
if command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="python3"
elif command -v python >/dev/null 2>&1; then
  PYTHON_BIN="python"
elif command -v python.exe >/dev/null 2>&1; then
  PYTHON_BIN="python.exe"
else
  # Fallback to node if python is completely missing from bash PATH
  PYTHON_BIN="$NODE_BIN"
fi

(cd "$CAPITOL_DIR" && "$NODE_BIN" scripts/capitol_creds_refresh.js)

# Use python (or node) to parse JSON and emit env format
if [[ "$PYTHON_BIN" == *"python"* ]]; then
  (cd "$CAPITOL_DIR" && "$PYTHON_BIN" -c "
import json, sys, os, configparser
from pathlib import Path

def update_aws_file(path, section, updates):
    config = configparser.RawConfigParser()
    if path.exists():
        try:
            config.read(path)
        except Exception as e:
            print(f'Warning: Could not read {path}: {e}')
    
    if section not in config.sections():
        config.add_section(section)
    
    for key, value in updates.items():
        config.set(section, key, value)
    
    # Remove session_token if it's not in updates (for permanent keys, though not our case here)
    if section == 'default' and 'aws_session_token' not in updates and config.has_option(section, 'aws_session_token'):
        config.remove_option(section, 'aws_session_token')

    with open(path, 'w') as f:
        config.write(f)
    print(f'Successfully updated {path} [{section}]')

try:
    with open('output/capitol_creds_refresh.json', 'r') as f:
        d = json.load(f)
    
    output_file = sys.argv[1]
    ak = d['accessKeyId']
    sk = d['secretAccessKey']
    st = d['sessionToken']
    region = 'us-east-1'

    # 1. Write .aws-session for manual sourcing
    lines = [
        f'aws_access_key_id={ak}',
        f'aws_secret_access_key={sk}',
        f'aws_session_token={st}',
        f'aws_region={region}',
        f'AWS_ACCESS_KEY_ID={ak}',
        f'AWS_SECRET_ACCESS_KEY={sk}',
        f'AWS_SESSION_TOKEN={st}',
        f'AWS_REGION={region}',
    ]
    with open(output_file, 'w') as f:
        f.write('\n'.join(lines) + '\n')
    # 2. Update AWS profile files directly
    import os
    from pathlib import Path

    def update_file_manual(file_path, section_name, key_values):
        print(f'Attempting to update {file_path}...')
        lines = []
        if file_path.exists():
            with open(file_path, 'r') as f:
                lines = f.readlines()

        new_lines = []
        skip_section = False
        section_found = False

        # Remove old section if it exists
        for line in lines:
            trimmed = line.strip()
            if trimmed.startswith('[') and trimmed.endswith(']'):
                current_section = trimmed[1:-1].strip()
                if current_section == section_name:
                    skip_section = True
                    section_found = True
                    continue
                else:
                    skip_section = False

            if not skip_section:
                new_lines.append(line)

        # Add new section
        if not new_lines or not new_lines[-1].strip() == '':
            new_lines.append('\n')

        new_lines.append(f'[{section_name}]\n')
        for k, v in key_values.items():
            new_lines.append(f'{k} = {v}\n')

        with open(file_path, 'w') as f:
            f.writelines(new_lines)
        print(f'Successfully updated {file_path} [{section_name}]')

    try:
        import os, subprocess
        from pathlib import Path
        
        # Get true Windows USERPROFILE via cmd.exe to bypass Bash mapping
        try:
            u_profile = subprocess.check_output(['cmd.exe', '/c', 'echo %USERPROFILE%'], 
                                              stderr=subprocess.STDOUT).decode().strip()
            # Normalize slashes for Windows (using os.sep to avoid escaping issues in bash string)
            u_profile = u_profile.replace(os.sep, '/')
        except:
            u_profile = os.environ.get('USERPROFILE') or os.path.expanduser('~')
            
        aws_dir = Path(u_profile) / '.aws'
        print(f'Targeting AWS directory: {aws_dir}')
        
        # Ensure directory exists with parents
        aws_dir.mkdir(parents=True, exist_ok=True)
        
        update_file_manual(aws_dir / 'credentials', 'default', {
            'aws_access_key_id': ak,
            'aws_secret_access_key': sk,
            'aws_session_token': st
        })
        
        update_file_manual(aws_dir / 'config', 'default', {
            'region': region,
            'output': 'json'
        })
    except Exception as e:
        print(f'Warning: Manual file update failed: {e}')

    print('')
    print('=== AWS Bedrock Session ===')

    print('')
    print('=== AWS Bedrock Session ===')
    print(f'Role:       {d.get(\"role\", \"N/A\")}')
    print(f'Expiration: {d.get(\"expiration\", \"N/A\")}')
    print(f'Key ID:     {ak}')
    print('')
    print(f'Written to: {os.path.basename(output_file)}')
    print('')
    print('--- For Claude Code ---')
    print(f'AWS_ACCESS_KEY_ID={ak}')
    print(f'AWS_SECRET_ACCESS_KEY={sk}')
    print(f'AWS_SESSION_TOKEN={st}')
    print(f'AWS_REGION={region}')
except Exception as e:
    print(f'Error updating credentials: {e}')
    import traceback
    traceback.print_exc()
    sys.exit(1)
" "$OUTPUT_FILE")
else
  # Node.js fallback (simplified, just env file)
  (cd "$CAPITOL_DIR" && "$NODE_BIN" -e "
const fs = require('fs');
const path = require('path');
try {
  const d = JSON.parse(fs.readFileSync('output/capitol_creds_refresh.json', 'utf8'));
  const lines = [
    'export AWS_ACCESS_KEY_ID=' + d.accessKeyId,
    'export AWS_SECRET_ACCESS_KEY=' + d.secretAccessKey,
    'export AWS_SESSION_TOKEN=' + d.sessionToken,
    'export AWS_REGION=us-east-1',
  ].join('\n');
  fs.writeFileSync(process.argv[1], lines + '\n');
  
  console.log('\n=== AWS Bedrock Session ===');
  console.log('Role:       ' + d.role);
  console.log('Key ID:     ' + d.accessKeyId);
  console.log('\nUpdated .aws-session file.');
  console.log('Note: Automatic ~/.aws/credentials update requires Python fallback.');
} catch (e) {
  console.error('Error:', e.message);
  process.exit(1);
}
" "$OUTPUT_FILE")
fi
