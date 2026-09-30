# Troubleshooting Log

Real issues hit while building this lab, in the order they came up, with root cause and fix. Kept here instead of buried in a wall of text in the README — useful for anyone rebuilding this setup, and doubles as a "things I actually debugged" reference for interviews or a defense/soutenance.

---

## 1. Disk full during Wazuh install (`error creating directory ... No space left`)

**Symptom:** `wazuh-install.sh` failed at the dashboard step with a disk-full error, even though the VM was given 40 GB.

**Root cause:** Ubuntu Server's default installer only allocates a small slice of the disk to the root LVM volume and leaves the rest unallocated.

**Fix:**
```bash
sudo lvextend -l +100%FREE /dev/ubuntu-vg/ubuntu-lv
sudo resize2fs /dev/mapper/ubuntu--vg-ubuntu--lv
df -h /
```
Ran into the same thing on the MISP and Shuffle VMs later — same fix each time.

---

## 2. `docker-compose` v1 crash: `KeyError: 'ContainerConfig'`

**Symptom:** `docker-compose up -d` for TheHive crashed with a Python traceback ending in `KeyError: 'ContainerConfig'` when recreating containers.

**Root cause:** Known bug in `docker-compose` v1 (the Python implementation) when it tries to recreate a container against a newer Docker Engine.

**Fix:** Drop v1, use the Docker-maintained v2 plugin (space instead of hyphen):
```bash
sudo apt install docker-compose-v2 -y
docker compose up -d   # not docker-compose
```

---

## 3. Cortex crash: `unrecognized option: -E`

**Symptom:** TheHive and Cassandra/Elasticsearch came up fine, Cortex kept restarting.

**Root cause:** The `docker-compose.yml` command arguments (`--es-hostnames`, `-E search.uri=...`) were copied from TheHive's syntax, but Cortex 3.1.7's CLI doesn't recognize them.

**Fix:** `docker logs <container>` showed Cortex printing its own `--help` output with the actual accepted flags. Switched to `--es-hostname` (singular) and dropped the unsupported `-E` flags.

**Takeaway:** when a container crash-loops, `docker logs` before touching the config again — it often prints exactly what it wanted.

---

## 4. MISP image pull rejected: `error from registry: denied`

**Symptom:** `docker compose up -d` for MISP failed pulling `ghcr.io/misp/misp:latest` — registry auth error even for what should be a public image.

**Fix:** Switched to `coolacid/misp-docker:core-latest` on Docker Hub, which pulls without authentication and is a well-maintained, widely used MISP image.

---

## 5. MISP container exits immediately: `CRON_USER_ID` format error

**Symptom:** `db` and `redis` stayed `Up`, `misp` container exited right after start.

**Root cause:** `docker logs` showed Supervisor failing to expand an undefined environment variable `ENV_CRON_USER_ID` referenced in its internal config.

**Fix:** Added `CRON_USER_ID=33` to the `environment:` block in `misp-server/docker-compose.yml`.

---

## 6. OOM kills in Shuffle (OpenSearch)

**Symptom:** `docker compose up -d` for Shuffle would hang indefinitely on `opensearch`, then the whole SSH session would become unresponsive.

**Root cause:** OpenSearch (bundled with Shuffle) needs real memory headroom to initialize; the Shuffle VM only had 2 GB RAM, and the Linux OOM killer was terminating the Java process mid-boot.

**Fix:**
- Bumped the VM to 4–6 GB RAM.
- Set the required kernel parameter (OpenSearch/Elasticsearch both need this):
  ```bash
  sudo sysctl -w vm.max_map_count=262144
  echo "vm.max_map_count=262144" | sudo tee -a /etc/sysctl.conf
  ```
- Brought services up in stages instead of all at once: `docker compose up -d opensearch`, wait, then `docker compose up -d` for the rest.

---

## 7. Wazuh ↔ Windows alerts not appearing — clock drift

**Symptom:** The Wazuh agent showed `Active`, `ossec.log` showed a clean connection, but no new alerts appeared in the dashboard no matter the time filter.

**Root cause:** The Wazuh server's clock had drifted roughly an hour off the Windows target's clock (both VMs had been suspended/resumed several times). Wazuh indexes alerts by timestamp; events arriving with a timestamp the dashboard considers "in the past" relative to its filter don't show up.

**Fix:**
```bash
sudo timedatectl set-ntp false
sudo timedatectl set-time "HH:MM:SS"   # match the Windows clock exactly
```
Then restarted `wazuh-manager` and re-triggered the test alert.

**Takeaway:** always check host clock sync first when a fully-connected agent produces zero alerts — it's the single most common false "nothing is working" symptom in a suspend/resume-heavy VM lab.

---

## 8. Wazuh → TheHive direct integration: `File not found inside 'integrations'`

**Symptom:** `ossec.log` showed `Unable to enable integration for: 'thehive'. File not found inside 'integrations'.`

**Root cause:** Wazuh's `<integration><name>thehive</name>` expects a built-in integration script that ships with certain Wazuh versions; it wasn't present on this install.

**Fix (superseded later by Shuffle, see `wazuh-server/legacy/`):** wrote a custom integration script (`custom-thehive.py`), referenced it as `<name>custom-thehive</name>`, and gave it execute permission with correct ownership (`chmod 755`, `chown root:wazuh`).

Follow-up bug: the script initially threw `json.decoder.JSONDecodeError` because Wazuh was sending the alert as a plain key=value string (`alert_format` not set to `json`). Fixed by adding `<alert_format>json</alert_format>` to the integration block.

---

## 9. Shuffle → TheHive: `Invalid json` — raw newlines in the description

**Symptom:** TheHive returned `400 Invalid json — Unexpected character ('n' (code 110))`.

**Root cause:** The case description was built with literal `Enter` line breaks inside a JSON string value — valid text, invalid JSON (needs an escaped `\n`).

**Fix:** Either escape newlines explicitly (`\n`) when writing raw JSON, or — the approach that turned out most reliable — switch the TheHive node to **Advanced** mode and write the JSON body directly rather than relying on the visual "Simple" field mapper, which had its own separate bug (see next entry).

---

## 10. Shuffle "Simple" Create Case action: `unexpected keyword argument 'start...'`

**Symptom:** `post_create_case()` failed with `TheHive....post_create_case() got an unexpected keyword argument 'start...'`, even with every visible field left empty.

**Root cause:** The Simple-mode form for this action includes a `Startdate` field that the underlying Python wrapper (`thehive4py`) doesn't accept as a keyword argument in this app version — a version mismatch baked into the node itself, not something fixable from the UI.

**Fix:** Switched the node to **Advanced** mode and wrote the case payload as raw JSON, bypassing the broken field mapper entirely.

---

## 11. MISP enrichment: array-variable resolution inconsistency

This was the longest one. Goal: pull the SHA256 out of the raw Sysmon log and pass it to MISP's `restSearch`.

- **Attempt 1 — Shuffle's built-in Regex Capture action:** returned `group_0` as an array. Referencing `$shuffle_tools_1.group_0[0]` worked in manual **Test Action** runs but failed (`405 Invalid JSON input`) on real webhook-triggered executions — the same node resolved the variable differently depending on how it was invoked.
- **Debugging:** added a temporary "Repeat back to me" node to print the raw resolved value. It showed the array being rendered *with its brackets and quotes still in the string* (`["HASH"]` instead of `HASH`) — which breaks the JSON payload MISP expects.
- **Fix that stuck:** replaced the Regex Capture action with a **Python node** (`execute_python`):
  ```python
  import re
  raw_hashes = "$exec.all_fields.data.win.eventdata.hashes"
  match = re.search(r"SHA256=([A-F0-9]+)", raw_hashes)
  sha256 = match.group(1) if match else "NOT_FOUND"
  print(sha256)
  ```
  A Python action's `print()` output resolves as a single clean string (`$shuffle_tools_1.message`), with none of the array-serialization ambiguity. This was consistent across manual tests and live webhook runs.

**Takeaway:** when a no-code node's output type is ambiguous (array vs. string) and behaves differently between test and production triggers, dropping to a two-line script is often faster than fighting the visual mapper.

---

## 12. MISP `restSearch`: 405 with valid-looking JSON

**Symptom:** Even with a clean hash string, MISP returned `405 Invalid JSON input`.

**Root cause:** The `Authorization` header wasn't being attached automatically by Shuffle's generic "Custom action" node the way it is for built-in actions — the MISP API key has to be added to the `Headers` field explicitly.

**Fix:** Added it directly:
```
Content-Type:application/json
Accept:application/json
Authorization:<YOUR_MISP_API_KEY>
```

---

## 13. Reading a nested match back out of MISP's response

Once MISP enrichment worked (`200`, with a real `Attribute` match), the next problem was pulling `value` / `comment` / `Event.info` back out of the response to put in the case description — same class of problem as #11 (nested array access).

**What didn't work:** `.0.value`, `[0].value`, and a Python node that tried to `json.loads()` the object (kept hitting `Syntax Error` / `Expecting value: line 1 column 1` — Shuffle wasn't substituting the multi-level object into the Python string cleanly).

**What worked:** typing `$` inside the field and using Shuffle's **autocomplete** instead of guessing syntax by hand. It surfaced the actual supported form:
```
$misp_1.body.response.Attribute.#.value
$misp_1.body.response.Attribute.#.comment
```
The `#` is Shuffle's own wildcard/first-index token for this kind of nested array — not `0`, not `[0]`.

**Takeaway:** for any deeply nested variable in Shuffle, trust the autocomplete suggestion over docs or intuition — the exact resolution syntax varies by how the upstream node structures its JSON.

---

## 14. Duplicate cases risk (legacy integration left enabled)

**Symptom:** none observed directly, caught before it became one — noticed that both `<name>custom-thehive</name>` (direct script, #8 above) and `<name>shuffle</name>` were enabled in `ossec.conf` at the same time. Any alert at level ≥ 10 would have triggered **two** separate case-creation paths.

**Fix:** commented out the `custom-thehive` integration block once the Shuffle pipeline was validated end to end, keeping it in the file (commented) as a reference for the legacy approach. Verified with a live attack that exactly one case was created afterward.

---

## 15. MISP enrichment returning empty by design (not a bug)

Once the pipeline was fully wired, MISP consistently returned `"Attribute": []` — which looked like a leftover bug, but is expected: a fresh MISP instance has no threat feeds, so a legitimate Windows binary's hash (`reg.exe`) will never match anything.

**To validate the "match found" path**, the attack's SHA256 was manually added as a MISP `Attribute` under a test event, and the event was **published** (MISP's REST search only returns attributes belonging to published events by default). Re-running the attack then produced a real match, proving the enrichment step behaves correctly in both the "known" and "unknown" cases — not just the empty one.
