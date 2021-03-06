
from zope.interface.verify import verifyObject

from testtools.matchers import IsInstance

from twisted.internet.interfaces import IProtocolFactory
from twisted.web.resource import IResource, Resource
from twisted.web.server import Site
from twisted.python.filepath import FilePath
from twisted.test.proto_helpers import MemoryReactor

from lae_site.handlers import make_resource, make_site
from lae_site.main import SiteOptions, site_for_options

from lae_util.testtools import TestCase


class MakeResourceTests(TestCase):
    """
    Tests for ``make_resource``.
    """
    def test_interface(self):
        """
        ``make_resource`` returns an ``IResource`` provider.
        """
        p = FilePath(self.mktemp().decode("ascii"))
        p.makedirs()
        resource = make_resource(
            u"stripe-secret-api-key",
            u"stripe-publishable-api-key",
            p.child(u"confirmed"),
            p.child(u"subscriptions"),
        )
        verifyObject(IResource, resource)


class MakeSiteTests(TestCase):
    """
    Tests for ``make_site``.
    """
    def test_interface(self):
        """
        ``make_site`` returns an ``IProtocolFactory`` provider.
        """
        logs = FilePath(self.mktemp().decode("ascii"))
        site = make_site(Resource(), logs)
        verifyObject(IProtocolFactory, site)



class SiteForOptionsTests(TestCase):
    """
    Tests for ``site_for_options``.
    """
    def test_stuff(self):
        p = FilePath(self.mktemp().decode("ascii"))
        p.makedirs()
        stripe_secret = p.child(b"stripe-key.secret")
        stripe_secret.setContent(b"foo")
        stripe_publishable = p.child(b"stripe-key.publishable")
        stripe_publishable.setContent(b"bar")

        options = SiteOptions(MemoryReactor())
        options.parseOptions([
            b"--signup-furl-path", b"signup.furl",
            b"--stripe-secret-api-key-path", stripe_secret.path,
            b"--stripe-publishable-api-key-path", stripe_publishable.path,
            b"--subscriptions-path", b"subscriptions.csv",
            b"--service-confirmed-path", b"confirmed.csv",
            b"--site-logs-path", b"httpd.log",
            b"--secure-port", "tcp:0",
        ])
        site = site_for_options(options)
        # XXX Very weak assertion...
        self.assertThat(site, IsInstance(Site))
