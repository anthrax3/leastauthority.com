#!/usr/bin/env python

if __name__ == '__main__':
    from sys import argv
    from twisted.internet.task import react
    from full_signup import main
    react(main, argv[1:])

import simplejson, sys

from twisted.python.log import startLogging
from twisted.python.usage import UsageError, Options
from twisted.python.filepath import FilePath
from twisted.internet import defer

from lae_automation.config import Config
from lae_automation.signup import DeploymentConfiguration
from lae_automation.signup import create_log_filepaths
from lae_automation.signup import lookup_product
from lae_automation.signup import get_bucket_name
from lae_automation.signup import activate_subscribed_service
from lae_util.streams import LoggingStream
from lae_util.servers import append_record
from lae_util.fileutil import make_dirs

def activate(secrets_dir, automation_config_path, server_info_path, stdin, flapp_stdout_path, flapp_stderr_path):
    append_record(flapp_stdout_path, "Automation script started.")
    parameters_json = stdin.read()
    (customer_email,
     customer_pgpinfo,
     customer_id,
     plan_id,
     subscription_id) = simplejson.loads(parameters_json)

    (abslogdir_fp,
    stripesecrets_log_fp,
    SSEC2secrets_log_fp,
    signup_log_fp) = create_log_filepaths(
        secrets_dir, plan_id, customer_id, subscription_id,
    )

    append_record(flapp_stdout_path, "Writing logs to %r." % (abslogdir_fp.path,))

    stripesecrets_log_fp.setContent(parameters_json)

    SSEC2_secretsfile = SSEC2secrets_log_fp.open('a+')
    signup_logfile = signup_log_fp.open('a+')
    signup_stdout = LoggingStream(signup_logfile, '>')
    signup_stderr = LoggingStream(signup_logfile, '')
    sys.stdout = signup_stderr

    def errhandler(err):
        with flapp_stderr_path.open("a") as fh:
            err.printTraceback(fh)
        return err

    with flapp_stderr_path.open("a") as stderr:
        print >>stderr, "plan_id is %s" % plan_id

    config = Config(automation_config_path.path)
    product = lookup_product(config, plan_id)
    fullname = product['plan_name']

    with flapp_stdout_path.open("a") as stdout:
        print >>stdout, "Signing up customer for %s..." % (fullname,)

    deploy_config = DeploymentConfiguration(
        products=config.products,
        s3_access_key_id=config.other["s3_access_key_id"],
        s3_secret_key=FilePath(config.other["s3_secret_path"]).getContent().strip(),
        bucketname=get_bucket_name(subscription_id, customer_id),
        amiimageid=product['ami_image_id'],
        instancesize=product['instance_size'],

        log_gatherer_furl=config.other.get("log_gatherer_furl") or None,
        stats_gatherer_furl=config.other.get("stats_gatherer_furl") or None,

        usertoken=None,
        producttoken=None,

        oldsecrets=None,
        customer_email=customer_email,
        customer_pgpinfo=customer_pgpinfo,
        secretsfile=SSEC2_secretsfile,
        serverinfopath=server_info_path.path,

        ssec2_access_key_id=config.other["ssec2_access_key_id"],
        ssec2_secret_path=config.other["ssec2_secret_path"],

        ssec2admin_keypair_name=config.other["ssec2admin_keypair_name"],
        ssec2admin_privkey_path=config.other["ssec2admin_privkey_path"],

        monitor_pubkey_path=config.other["monitor_pubkey_path"],
        monitor_privkey_path=config.other["monitor_privkey_path"],
    )
    d = defer.succeed(None)
    d.addCallback(lambda ign: activate_subscribed_service(
        deploy_config, signup_stdout, signup_stderr, signup_log_fp.path,
    ))
    d.addErrback(errhandler)
    d.addBoth(lambda ign: signup_logfile.close())
    return d


class SignupOptions(Options):
    optParameters = [
        ("log-directory", None, None, "Path to a directory to which to write signup logs.", FilePath),
        ("secrets-directory", None, None, "Path to a directory to which to write subscription secrets.", FilePath),

        ("automation-config-path", None, None, "Path to an lae_automation_config.json file.", FilePath),
        ("server-info-path", None, None, "Path to a file to write new server details to.", FilePath),
    ]


def main(reactor, *argv):
    o = SignupOptions()
    try:
        o.parseOptions(argv)
    except UsageError as e:
        raise SystemExit(str(e))

    defer.setDebugging(True)

    log_dir = o["log-directory"]
    secrets_dir = o["secrets-directory"]

    for d in [log_dir, secrets_dir]:
        if not log_dir.isdir():
            make_dirs(log_dir.path)

    flapp_stdout_path = log_dir.child('stdout')
    flapp_stderr_path = log_dir.child('stderr')

    startLogging(flapp_stdout_path.open("a"), setStdout=False)

    return activate(
        secrets_dir,
        o["automation-config-path"], o["server-info-path"],
        sys.stdin, flapp_stdout_path, flapp_stderr_path,
    )
