#!/usr/bin/python2.7

import sys

MIN_PYTHON = (2, 7)
if sys.version_info < MIN_PYTHON:
    sys.exit("Python %s.%s or later is required.\n" % MIN_PYTHON)

import argparse
import botocore.session
import botocore.auth
import botocore.awsrequest
import errno
import json
import os
import requests
import subprocess
import syslog
import time

# The default Chronicle version.
# Should be updated with each new Chronicle release
DEFAULT_CHRONICLE_VERSION = "chronicled-2.0.1228.0-1_naws"

# The aws-sec-informatics RPM signing key.
PUBLIC_KEY = """-----BEGIN PGP PUBLIC KEY BLOCK-----
Version: GnuPG v2

mQINBFzKMpgBEACrasdQqYhqFEV5dNE34FpkY6xZ1RXiplb//aVIGZagkuuhRchU
AwQwWAoVqj4+YUYdObBQmGgu9if1kMTmo4vA7lyjaol/fRNQW7abDbvLUeHObzrz
c5aPvYz5yy4kXM6pTvmFHWDx74+AhUNkklRFDxhpAX5wIBzGnMtQFu0tKezFdwXj
OSCBooDNVKqRXXwi+qwRedhLevGHOLeB3PmUPl4nukEf26IH18UN4WGl5s1SAlei
bD+OkA6Xp5M5FWgmeFcD9YjM7J2tVD80P/4TMwQa9AfYX8yX0jHWSdIyVMcwGsRY
fXMWvt3VkOLFKCOugWCFr6xj+ogHfTXFo0YN9kKKTdBkqgtcdC0HgIEOzdJVsVuO
tQKNjSalzs7tceyqAMW0+zu80TXKXLk6HL41TUY5nptfyWn9sKaWTY74qYAT1WGy
h1Byn+DBFD4BI9uO3CLreDF9oYCcVzPJzAQ4OulvpOuMX3U+J5nMRO0DZeoavFbb
4WpjeobkXB9L2V8tkqNTY1Vn4S/MinpRmUmsQJFjCAG/ZcTejiCJI9mc1/mCcihX
LGOudztnnYkLBbK9Nmux+tE0LRThIcM8F6JnC7Y42fjEn4xnpjpi+C5p5lA80xF2
JVkr+xquoprKasp+/mYEPQX9NwbXzkRKY6S4tJ1bX9H7G6WI7Ynvp6MCJwARAQAB
tDRhd3Mtc2VjLWluZm9ybWF0aWNzIDxhd3Mtc2VjLWluZm9ybWF0aWNzQGFtYXpv
bi5jb20+iQI5BBMBCAAjBQJcyjKYAhsDBwsJCAcDAgEGFQgCCQoLBBYCAwECHgEC
F4AACgkQxsHtsCiC8moNyA/+OadXPTpDE55B9sCQmd0HBRnAN++GJMmxYclLc6Yv
Y9G7sG0b3TaT7KMgr6Mfn73LF59Aq8KfGvydQYuEfp/ig68+G3Bf4/XluPwrywMg
dlWefHfd1pcOkIAQA5hhE1ApviAGDrvJYFGx7o1lj4Aw0QPBFNcVCmWsbV8zY3pa
MuYm+sAA/TQsjtO9RhUi/aShTEBfUVv0pFNMJc5aYNhHnpiJUHpEKgfcYJIjMXsi
zpkngeZoHvULDoPP8CPlHmPvR9zrj5EH1UnBsmvigcK9Dlcl3xv5NwnL7Gv7xMVc
d0Y97nWAqRMpSJW2aJWE5yGSEYivi/VQXFCUdd7I0FNZJg98w2iP69HYV+WKdbxJ
+T45yHGPtFyEYiEuiUt7idJzfE2zx0Wv0j3tVBXsS2w2gKvsRQoP/tdiiqbaTkCz
LJO7szQAu1PrrBzACqNp78EdWUgnFamLD+Fr++VHXemPiDDCisLWuXV/4oS+MMCL
kL1ylOZWbZ+DZ4yBgUMgValIAjfNQ3LZ/XmRY7iOsoPULhHExlkuKk5PzRvNcWnR
mMM/Qm+lzQt+SbU/I2uy9tSLvPHZYlHTy/3OMk7wBVKL2GSY4fUU1f4GiePs6fZT
WZ8WYQO4gb7PBNqz4SDOETE5ntvXrUrg5AaNLBxCtn7K5ExD03bbk2n2HUVRI3YB
z5s=
=+Q8r
-----END PGP PUBLIC KEY BLOCK-----
"""

CHRONICLE_DIR = "/usr/local/chronicle"
RPM_PATH = "/usr/local/chronicle/chronicled.rpm"
KEY_PATH = "/usr/local/chronicle/public_key"

error_exit_code = 0

def log(msg):
    print(msg)
    # 80 is LOG_AUTHPRIV
    syslog.syslog(80 | syslog.LOG_ERR, msg)


def log_exit(msg):
    log(msg)
    sys.exit(error_exit_code)


def decode_output(error):
    # In python 3, the subprocess output is in bytes but is a
    # string in python 2 so we try to convert.
    output = error.output
    try:
        output = output.decode()
    except (UnicodeDecodeError, AttributeError):
        pass
    return output


def get_region():
    # try to use IMDSv2, if it's available
    response = requests.put(
        "http://169.254.169.254/latest/api/token",
        headers={"X-aws-ec2-metadata-token-ttl-seconds": "21600"},
    )
    headers = {}
    if response.status_code == requests.codes.ok:
        headers["X-aws-ec2-metadata-token"] = response.content

    response = requests.get(
        "http://169.254.169.254/latest/dynamic/instance-identity/document",
        headers=headers,
    )
    if response.status_code != requests.codes.ok:
        log_exit(
            "bad response when getting instance-identity document: %s"
            % response.status_code
        )
    data = response.content
    try:
        data = data.decode()
    except (UnicodeDecodeError, AttributeError):
        pass
    return json.loads(data)["region"]


def is_audit_installed():
    try:
        subprocess.check_output(["rpm", "-q", "audit"], stderr=subprocess.STDOUT)
        return True
    except subprocess.CalledProcessError:
        return False


def remove_audit():
    # Removing the audit package doesn't always stop the daemon,
    # so do that first.
    subprocess.call(["/sbin/service", "auditd", "stop"])

    try:
        subprocess.check_output(
            ["yum", "-y", "remove", "audit"], stderr=subprocess.STDOUT
        )
    except subprocess.CalledProcessError as e:
        output = decode_output(e)
        log("Unable to remove audit: %s" % output)


def make_dir():
    try:
        os.makedirs(CHRONICLE_DIR, 0o700)
    except OSError as e:
        if e.errno != errno.EEXIST:
            log_exit(str(e))

        # If the directory already exists, just make sure it's got the
        # right mode.
        os.chmod(CHRONICLE_DIR, 0o700)


def attempt_get(retry_count, url, **args):
    attempts = 0
    backoff_factor = 0.2
    while attempts < retry_count:
        attempts += 1
        try:
            results = requests.get(url, **args)
            if results.status_code != requests.codes.ok:
                if results.status_code in [500, 503]:
                    time.sleep(4)
                    raise Exception(
                        "potential throttling, got response code: %s"
                        % results.status_code
                    )
                elif results.status_code > 400:
                    raise Exception("unexpected response code: %s" % results.status_code)
            return results
        except Exception as e:
            # Here we want to wait to give the network time to recover
            log(str(e))
            time.sleep(backoff_factor * (2 ** (attempts - 1)))
    # On our last attempt, we want to just pass the request back up the
    # call stack
    return requests.get(url, **args)


def install_rpm():
    # In order to support pipeline rollbacks, try to downgrade if install
    # comes back with nothing to do.
    for method in ("install", "downgrade"):
        try:
            subprocess.check_output(
                ["yum", "-y", method, RPM_PATH], stderr=subprocess.STDOUT
            )
            return
        except subprocess.CalledProcessError as e:
            output = decode_output(e)
            if "Nothing to do" not in output:
                log_exit("Unable to install chronicle: %s" % output)


def download_rpm(ver):
    # The RPM is stored in a private S3 bucket.  There are too many internal
    # accounts to grant them all access via a bucket policy.  The Chronicle
    # control service has an endpoint that will return a presigned URL to the
    # current RPM, after authenticating and authorizing this account.  This
    # assumes that the EC2 instance has an instance role so we can get its
    # instance credentials.
    region = get_region()
    if region == "us-iso-east-1":
        remote_host = "https://chronicle-control-prod.%s.c2s.ic.gov" % region
    elif region == "us-isob-east-1":
        remote_host = "https://chronicle-control-prod.%s.sc2s.sgov.gov" % region
    elif region == "cn-north-1" or region == "cn-northwest-1":
        remote_host = "https://control.prod.%s.chronicle.security.aws.a2z.org.cn" % region
    else:
        remote_host = "https://control.prod.%s.chronicle.security.aws.a2z.com" % region
    url = "%s/rpm/%s" % (remote_host, ver)

    s = botocore.session.Session()
    r = botocore.awsrequest.AWSRequest(method="GET", url=url, data="")
    botocore.auth.SigV4Auth(
        s.get_credentials(), "aws-chronicle-collection", region
    ).add_auth(r)
    p = r.prepare()
    mvp_ca_bundle = "/etc/pki/%s/certs/ca-bundle.pem" % region
    if region.startswith("us-iso"):
        response = attempt_get(
            5, p.url, headers=p.headers, verify=mvp_ca_bundle, timeout=10
        )
    else:
        response = attempt_get(5, p.url, headers=p.headers, timeout=10)

    if response.status_code != requests.codes.ok:
        log_exit("bad response when getting RPM url: %s" % response.status_code)
    presigned_url = response.content

    if region.startswith("us-iso"):
        response = attempt_get(5, presigned_url, verify=mvp_ca_bundle, timeout=10)
    else:
        response = attempt_get(5, presigned_url, timeout=10)

    if response.status_code != requests.codes.ok:
        log_exit("bad response when getting RPM contents: %s" % response.status_code)
    data = response.content

    with open(RPM_PATH, "wb") as outf:
        outf.write(data)


def verify_rpm():
    # Install the public key.
    with open(KEY_PATH, "wb") as outf:
        outf.write(PUBLIC_KEY.encode("utf-8"))
    try:
        subprocess.check_output(["rpm", "--import", KEY_PATH], stderr=subprocess.STDOUT)
    except subprocess.CalledProcessError as e:
        output = decode_output(e)
        log_exit("Unable to import public key: %s" % output)

    # Verify that the RPM is signed.
    try:
        sig = subprocess.check_output(
            ["rpm", "-qp", "--qf", "%{SIGPGP:pgpsig}", RPM_PATH]
        )
        # Python3 we need to decode, Python 2 returns a string so we do nothing
        try:
            sig = sig.decode()
        except (UnicodeDecodeError, AttributeError):
            pass
        if "c6c1edb02882f26a" not in sig:
            log_exit("RPM is not signed")
    except subprocess.CalledProcessError as e:
        output = decode_output(e)
        log_exit("Error reading signature from RPM: %s" % output)

    # Verify that the signature is valid.
    try:
        subprocess.check_output(["rpm", "--checksig", RPM_PATH], stderr=subprocess.STDOUT)
    except subprocess.CalledProcessError as e:
        output = decode_output(e)
        log_exit("RPM has invalid signature: %s" % output)


def check_rpmdb():
    # We're not going to try a full repair, but we can at least check for
    # stale locks.
    try:
        # If everything is working, this won't fail.
        subprocess.check_output(["rpm", "-q", "rpm"], stderr=subprocess.STDOUT)
        return
    except subprocess.CalledProcessError as e:
        output = decode_output(e)
        if "Thread died in Berkeley DB library" not in output:
            log_exit("error calling rpm: %s" % output)

    # A previous process left locks on the rpmdb.
    # Double check that nothing is currently using it.
    ret = subprocess.call("! fuser -u /var/lib/rpm/* 2>&1 | grep -q '(root)'", shell=True)
    if ret != 0:
        time.sleep(20)
        ret = subprocess.call(
            "! fuser -u /var/lib/rpm/* 2>&1 | grep -q '(root)'", shell=True
        )
        if ret != 0:
            # root has one of the file open, so we're not going to risk
            # trying to fix it.
            log_exit("error calling rpm: %s" % output)

    # Nothing is running.  Remove the stale lock files.
    files = os.listdir("/var/lib/rpm")
    for filename in files:
        if filename.startswith("__db"):
            os.remove(os.path.join("/var/lib/rpm", filename))


if __name__ == "__main__":
    try:
        arch = os.uname()[4]
        ver = "%s.%s.rpm" % (DEFAULT_CHRONICLE_VERSION, arch)

        parser = argparse.ArgumentParser(description="Install Chronicle")
        parser.add_argument("--latest", action="store_true",
                            help="Install the latest version instead of the default")
        parser.add_argument("--error-exit-code", default=0, type=int,
                            help="Exit code to return on error")
        args = parser.parse_args()
        error_exit_code = args.error_exit_code
        if args.latest:
            ver = arch

        make_dir()
        download_rpm(ver)
        check_rpmdb()

        try:
            verify_rpm()

            if is_audit_installed():
                remove_audit()

            install_rpm()

            log("chronicled installed")

        finally:
            # If the validation failed, make sure we don't leave a bad RPM lying
            # around that an admin could accidentally install.
            os.remove(RPM_PATH)

    except Exception as e:
        # Swallow errors so that we don't block instances from starting if this
        # script has been added to a userdata script that check for errors.
        log_exit("caught exception: %s" % str(e))
