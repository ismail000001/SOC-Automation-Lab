#!/var/ossec/framework/python/bin/python3
import sys, os, json, requests, uuid

debug_log = "/var/ossec/logs/custom-thehive-debug.log"
with open(debug_log, "a") as log:
    log.write(f"\n--- New Alert ---\nArgs: {sys.argv}\n")
    try:
        alert_file = sys.argv[1]
        api_key = sys.argv[2]
        hook_url = sys.argv[3]

        with open(alert_file, "r", encoding="utf-8") as f:
            raw_data = f.read()

        log.write(f"Raw Data: {raw_data[:200]}...\n")

        if not raw_data.strip():
            log.write("ERROR: File is completely empty!\n")
            sys.exit(1)

        alert = json.loads(raw_data)
        rule = alert.get('rule', {})
        desc = rule.get('description', 'Wazuh Alert')
        rule_id = rule.get('id', 'N/A')
        level = int(rule.get('level', 1))

        payload = {
            "title": f"Wazuh Alert: {desc}",
            "description": f"**Rule ID:** {rule_id}\n**Level:** {level}",
            "severity": 3 if level >= 8 else 2,
            "type": "wazuh",
            "source": "wazuh",
            "sourceRef": str(uuid.uuid4())[0:8],
            "tags": ["Wazuh", f"rule:{rule_id}"]
        }

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }

        log.write(f"Sending to TheHive...\n")
        resp = requests.post(f"{hook_url}/api/v1/alert", headers=headers, json=payload, verify=False)
        log.write(f"Response: {resp.status_code} - {resp.text}\n")

    except Exception as e:
        log.write(f"Exception: {str(e)}\n")
        sys.exit(1)