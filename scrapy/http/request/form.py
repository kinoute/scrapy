"""
This module implements the FormRequest class which is a more convenient class
(than Request) to generate Requests based on form data.

See documentation in docs/topics/request-response.rst
"""

import six
from six.moves.urllib.parse import urljoin, urlencode, urlsplit

import lxml.html
from parsel.selector import create_root_node
from w3lib.html import strip_html5_whitespace

from scrapy.http.request import Request
from scrapy.utils.python import to_bytes, is_listlike
from scrapy.utils.response import get_base_url


class FormRequest(Request):

    def __init__(self, *args, **kwargs):
        formdata = kwargs.pop('formdata', None)
        if formdata and kwargs.get('method') is None:
            kwargs['method'] = 'POST'

        super(FormRequest, self).__init__(*args, **kwargs)

        if formdata:
            items = formdata.items() if isinstance(formdata, dict) else formdata
            querystr = _urlencode(items, self.encoding)
            if self.method == 'POST':
                self.headers.setdefault(b'Content-Type', b'application/x-www-form-urlencoded')
                self._set_body(querystr)
            else:
                url_split = urlsplit(self.url)
                formdata_key_list = []
                for k in querystr.split('&'):
                    formdata_key_list.append(k.split('=')[0])

                if url_split.query:
                    queries = (url_split.query).split('&')
                    query_dict = {}

                    for x in queries:
                        query = x.split('=')
                        if formdata_key_list.count(query[0])==0:
                            query_dict[query[0]] = query[1]
                        query_str = ''
                        for k, v in query_dict:
                            query_str += (k + '=' + v + '&')

                    self._set_url(self.url[:self.url.index('?')+1] + query_str + querystr + ('#' + url_split.fragment if url_split.fragment else ''))
                else:
                    self._set_url(self.url + '?' + querystr + ('#' + url_split.fragment if url_split.fragment else ''))

    @classmethod
    def from_response(cls, response, formname=None, formid=None, formnumber=0, formdata=None,
                      clickdata=None, dont_click=False, formxpath=None, formcss=None, **kwargs):

        kwargs.setdefault('encoding', response.encoding)

        if formcss is not None:
            from parsel.csstranslator import HTMLTranslator
            formxpath = HTMLTranslator().css_to_xpath(formcss)

        form = _get_form(response, formname, formid, formnumber, formxpath)
        formdata = _get_inputs(form, formdata, dont_click, clickdata, response)
        url = _get_form_url(form, kwargs.pop('url', None))
        method = kwargs.pop('method', form.method)
        return cls(url=url, method=method, formdata=formdata, **kwargs)


def _get_form_url(form, url):
    if url is None:
        action = form.get('action')
        if action is None:
            return form.base_url
        return urljoin(form.base_url, strip_html5_whitespace(action))
    return urljoin(form.base_url, url)


def _urlencode(seq, enc):
    values = [(to_bytes(k, enc), to_bytes(v, enc))
              for k, vs in seq
              for v in (vs if is_listlike(vs) else [vs])]
    return urlencode(values, doseq=1)


def _get_form(response, formname, formid, formnumber, formxpath):
    """Find the form element """
    root = create_root_node(response.text, lxml.html.HTMLParser,
                            base_url=get_base_url(response))
    forms = root.xpath('//form')
    if not forms:
        raise ValueError("No <form> element found in %s" % response)

    if formname is not None:
        f = root.xpath('//form[@name="%s"]' % formname)
        if f:
            return f[0]

    if formid is not None:
        f = root.xpath('//form[@id="%s"]' % formid)
        if f:
            return f[0]

    # Get form element from xpath, if not found, go up
    if formxpath is not None:
        nodes = root.xpath(formxpath)
        if nodes:
            el = nodes[0]
            while True:
                if el.tag == 'form':
                    return el
                el = el.getparent()
                if el is None:
                    break
        encoded = formxpath if six.PY3 else formxpath.encode('unicode_escape')
        raise ValueError('No <form> element found with %s' % encoded)

    # If we get here, it means that either formname was None
    # or invalid
    if formnumber is not None:
        try:
            form = forms[formnumber]
        except IndexError:
            raise IndexError("Form number %d not found in %s" %
                             (formnumber, response))
        else:
            return form


def _get_inputs(form, formdata, dont_click, clickdata, response):
    try:
        formdata_keys = dict(formdata or ()).keys()
    except (ValueError, TypeError):
        raise ValueError('formdata should be a dict or iterable of tuples')

    if not formdata:
        formdata = ()
    inputs = form.xpath('descendant::textarea'
                        '|descendant::select'
                        '|descendant::input[not(@type) or @type['
                        ' not(re:test(., "^(?:submit|image|reset)$", "i"))'
                        ' and (../@checked or'
                        '  not(re:test(., "^(?:checkbox|radio)$", "i")))]]',
                        namespaces={
                            "re": "http://exslt.org/regular-expressions"})
    values = [(k, u'' if v is None else v)
              for k, v in (_value(e) for e in inputs)
              if k and k not in formdata_keys]

    if not dont_click:
        clickable = _get_clickable(clickdata, form)
        if clickable and clickable[0] not in formdata and not clickable[0] is None:
            values.append(clickable)

    if isinstance(formdata, dict):
        formdata = formdata.items()

    values.extend((k, v) for k, v in formdata if v is not None)
    return values


def _value(ele):
    n = ele.name
    v = ele.value
    if ele.tag == 'select':
        return _select_value(ele, n, v)
    return n, v


def _select_value(ele, n, v):
    multiple = ele.multiple
    if v is None and not multiple:
        # Match browser behaviour on simple select tag without options selected
        # And for select tags wihout options
        o = ele.value_options
        return (n, o[0]) if o else (None, None)
    elif v is not None and multiple:
        # This is a workround to bug in lxml fixed 2.3.1
        # fix https://github.com/lxml/lxml/commit/57f49eed82068a20da3db8f1b18ae00c1bab8b12#L1L1139
        selected_options = ele.xpath('.//option[@selected]')
        v = [(o.get('value') or o.text or u'').strip() for o in selected_options]
    return n, v


def _get_clickable(clickdata, form):
    """
    Returns the clickable element specified in clickdata,
    if the latter is given. If not, it returns the first
    clickable element found
    """
    clickables = [
        el for el in form.xpath(
            'descendant::input[re:test(@type, "^(submit|image)$", "i")]'
            '|descendant::button[not(@type) or re:test(@type, "^submit$", "i")]',
            namespaces={"re": "http://exslt.org/regular-expressions"})
        ]
    if not clickables:
        return

    # If we don't have clickdata, we just use the first clickable element
    if clickdata is None:
        el = clickables[0]
        return (el.get('name'), el.get('value') or '')

    # If clickdata is given, we compare it to the clickable elements to find a
    # match. We first look to see if the number is specified in clickdata,
    # because that uniquely identifies the element
    nr = clickdata.get('nr', None)
    if nr is not None:
        try:
            el = list(form.inputs)[nr]
        except IndexError:
            pass
        else:
            return (el.get('name'), el.get('value') or '')

    # We didn't find it, so now we build an XPath expression out of the other
    # arguments, because they can be used as such
    xpath = u'.//*' + \
            u''.join(u'[@%s="%s"]' % c for c in six.iteritems(clickdata))
    el = form.xpath(xpath)
    if len(el) == 1:
        return (el[0].get('name'), el[0].get('value') or '')
    elif len(el) > 1:
        raise ValueError("Multiple elements found (%r) matching the criteria "
                         "in clickdata: %r" % (el, clickdata))
    else:
        raise ValueError('No clickable element matching clickdata: %r' % (clickdata,))
