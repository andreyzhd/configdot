# -*- coding: utf-8 -*-
"""
Parse INI files into nested config objects.

@author: Jussi (jnu@iki.fi)
"""
import ast
import re
import pprint
import logging
import sys

logger = logging.getLogger(__name__)


# regexes for parsing
RE_ALPHANUMERIC = r'\w+$'  # at least 1 alphanumeric char
RE_WHITESPACE = r'\s*$'  # empty or whitespace
# match line comment; group 1 will be the comment
RE_COMMENT = r'\s*[#;]\s*(.*)'
# match item def; groups 1 and 2 are the item and the (possibly empty) value
RE_VAR_DEF = r'\s*([^=\s]+)\s*=\s*(.*?)\s*$'
# match section header of form [section]; group 1 is the section
# section names can include alphanumeric chars, _ and -
RE_SECTION_HEADER = r'\s*\[([\w-]+)\]\s*$'
# subsection header of form [[subsection]]
RE_SUBSECTION_HEADER = r'\s*\[\[([\w-]+)\]\]\s*$'


def _simple_match(r, s):
    """Match regex r against string s"""
    return bool(re.match(r, s))


def _is_comment(s):
    """Check if s is a comment"""
    return _simple_match(RE_COMMENT, s)


def _is_proper_varname(s):
    """Check if s is an acceptable variable name"""
    return _simple_match(RE_ALPHANUMERIC, s)


def _is_whitespace(s):
    """Check if s is whitespace only"""
    return _simple_match(RE_WHITESPACE, s)


def _parse_var_def(s):
    """Match (possibly partial) var definition.

    Return varname, val tuple if successful"""
    m = re.match(RE_VAR_DEF, s)
    if m:
        varname, val = m.group(1).strip(), m.group(2).strip()
        if _is_proper_varname(varname):
            return varname, val
    return None, None


def _parse_section_header(s):
    """Match section header of form [header] and return header as str"""
    m = re.match(RE_SECTION_HEADER, s)
    return m.group(1) if m else None


def _parse_subsection_header(s):
    """Match subsection header of form [[header]] and return header as str"""    
    m = re.match(RE_SUBSECTION_HEADER, s)
    return m.group(1) if m else None





def get_description(item_or_section):
    """Returns a description based on section or item comment.

    Parameters
    ----------
    item_or_section : ConfigContainer | ConfigItem
        Item or section.

    Returns
    -------
    str
        The description.

    Note: not implemented as an instance method to avoid polluting the class
    namespace.
    """
    desc = item_or_section._comment
    # currently just capitalizes first letter of comment string
    return desc[:1].upper() + desc[1:]


class ConfigItem:
    """Holds data for a config item"""

    def __init__(self, name=None, value=None, comment=None):
        if comment is None:
            comment = ''
        self._comment = comment
        self.name = name
        self.value = value

    def __repr__(self):
        return f'<ConfigItem| {self.name} = {self.value!r}>'

    @property
    def literal_value(self):
        """Returns a string that is supposed to evaluate to the value"""
        return repr(self.value)

    @property
    def item_def(self):
        """Prettyprint item definition"""
        return f'{self.name} = {pprint.pformat(self.value)}'


class ConfigContainer:
    """Holds config items (ConfigContainer or ConfigItem instances)"""

    def __init__(self, items=None, comment=None):
        # need to modify __dict__ directly to avoid infinite __setattr__ loop
        if items is None:
            items = dict()
        if comment is None:
            comment = ''
        self.__dict__['_items'] = items
        self.__dict__['_comment'] = comment

    def __contains__(self, item):
        """Checks items by name"""
        return item in self._items

    def __iter__(self):
        """Yields tuples of (item_name, item)"""
        for val in self._items.items():
            yield val

    def __getattr__(self, attr):
        """Returns an item by the syntax container.item.

        If the item is a ConfigItem instance, return the item value instead.
        This allows getting values directly.
        """
        try:
            item = self._items[attr]
        except KeyError:
            raise AttributeError(f"no such item or section: '{attr}'")
        return item.value if isinstance(item, ConfigItem) else item

    def __getitem__(self, item):
        """Returns an item"""
        return self._items[item]

    def __setattr__(self, attr, value):
        """Set attribute"""
        if isinstance(value, ConfigItem) or isinstance(value, ConfigContainer):
            # replace an existing section/item
            self.__dict__['_items'][attr] = value
        elif attr == '_comment':
            self.__dict__['_comment'] = value
        elif attr in self._items:
            # update value of existing item (syntax sec.item = value)
            self.__dict__['_items'][attr].value = value
        else:
            # implicitly create a new ConfigItem (syntax sec.item = value)
            self.__dict__['_items'][attr] = ConfigItem(name=attr, value=value)

    def __repr__(self):
        s = '<ConfigContainer|'
        s += ' items: '
        s += ', '.join(f"'{key}'" for key in self._items.keys())
        s += '>'
        return s


def parse_config(fname, encoding=None):
    """Parse a configuration file.

    Parameters:
    -----------
    fname : str
        The filename.
    encoding : str
        The encoding to use. By default, open() uses the preferred encoding of
        the locale. On Windows, this is still cp1252 and not utf-8. If your
        configuration files are in utf-8 (as they probably will be), you need to
        specify encoding='utf-8' to correctly read extended characters.

    Returns:
    -------
    ConfigContainer
        The config object.
    """
    if encoding is None and sys.platform == 'win32':
        logger.warning(
            "On Windows, you need to explicitly specify encoding='utf-8' "
            "if your config file is encoded with UTF-8."
        )
    with open(fname, 'r', encoding=encoding) as f:
        lines = f.read().splitlines()
    return _parse_config(lines)


def _parse_config(lines):
    """Parse INI file lines into a ConfigContainer instance.

    Supports:
        -multiline variable definitions
        -multiple comment lines per item/section
    Does not support:
        -inline comments (would be too confusing with multiline defs)
        -nested sections (though possible with ConfigContainer)
    """
    _comments = list()  # comments for current variable
    _def_lines = list()  # definition lines for current variable
    current_section = None
    ongoing_def = False
    config = ConfigContainer()

    for lnum, li in enumerate(lines, 1):
        # every line is either: comment, section header, variable definition,
        # continuation of variable definition, or whitespace

        secname = _parse_section_header(li)
        item_name, val = _parse_var_def(li)

        # new section
        if secname:
            if ongoing_def:  # did not finish previous definition
                raise ValueError(f'could not evaluate definition at line {lnum}')
            comment = ' '.join(_comments)
            current_section = ConfigContainer(comment=comment)
            setattr(config, secname, current_section)
            _comments = list()

        # new item definition
        elif item_name:
            if ongoing_def:
                raise ValueError(f'could not evaluate definition at line {lnum}')
            elif not current_section:
                raise ValueError(
                    f'item definition outside of any section on line {lnum}'
                )
            elif item_name in current_section:
                raise ValueError(f'duplicate definition on line {lnum}')
            try:
                val_eval = ast.literal_eval(val)
                # if eval is successful, record the variable
                comment = ' '.join(_comments)
                item = ConfigItem(comment=comment, name=item_name, value=val_eval)
                setattr(current_section, item_name, item)
                _comments = list()
                _def_lines = list()
                ongoing_def = None
            except (ValueError, SyntaxError):  # eval failed, continued def?
                ongoing_def = item_name
                _def_lines.append(val)
                continue

        elif _is_comment(li):
            if ongoing_def:
                raise ValueError(f'could not evaluate definition at line {lnum}')
            m = re.match(RE_COMMENT, li)
            cmnt = m.group(1)
            _comments.append(cmnt)

        elif _is_whitespace(li):
            if ongoing_def:
                raise ValueError(f'could not evaluate definition at line {lnum}')

        # either a continued def or a syntax error
        else:
            if not ongoing_def:
                raise ValueError(f'syntax error at line {lnum}: {li}')
            _def_lines.append(li.strip())
            try:
                val_new = ''.join(_def_lines)
                val_eval = ast.literal_eval(val_new)
                comment = ' '.join(_comments)
                item = ConfigItem(comment=comment, name=ongoing_def, value=val_eval)
                setattr(current_section, ongoing_def, item)
                _comments = list()
                _def_lines = list()
                ongoing_def = None
            except (ValueError, SyntaxError):  # cannot evaluate def (yet)
                continue

    if ongoing_def:  # we got to the end, but did not finish definition
        raise ValueError(f'could not evaluate definition at line {lnum}')
        
    return config


def update_config(
    cfg, cfg_new, create_new_sections=True, create_new_items=True, update_comments=False
):
    """Update existing Config instance from another.

    Parameters
    ----------
    cfg : ConfigContainer
        The original config (to be updated).
    cfg_new : ConfigContainer
        The config that contains the updated data.
    create_new_sections : bool
        Whether to create config sections that don't exist in the original config.
    create_new_items : bool
        Whether to create config items that don't exist in the original config. If False,
        will only update existing items.
    update_comments : bool
        If True, comments will be updated too.
    """
    for secname, sec in cfg_new:
        if isinstance(create_new_items, list):
            _create_new_items = secname in create_new_items
        else:
            _create_new_items = create_new_items
        if secname in cfg:
            # section exists, update the items
            sec_old = cfg[secname]
            if update_comments:
                sec_old._comment = sec._comment
            for itname, item in sec:
                if itname in sec_old:
                    # item exists, update
                    if update_comments:
                        setattr(sec_old, itname, item)
                    else:  # update value only
                        item_old = sec_old[itname]
                        item_old.value = item.value
                elif _create_new_items:
                    # item does not exist and can be created
                    setattr(sec_old, itname, item)
                else:
                    logger.warning(f'unknown config item: [{secname}]/{itname}')
        elif create_new_sections:
            # create nonexisting section anew
            setattr(cfg, secname, sec)
        else:
            logger.warning(f'unknown config section: {secname}')


def dump_config(cfg):
    """Return a config instance as text.

    Parameters
    ----------
    cfg : ConfigContainer
        The configuration.

    Returns
    -------
    string
        The configuration in string format.

    This function should return a string that reproduces the configuration when
    fed to _parse_config(). It can be used to e.g. write the config back into a
    file.
    """

    def _gen_dump(cfg):
        sects = sorted(cfg, key=lambda tup: tup[0])  # sort by name
        for k, (sectname, sect) in enumerate(sects):
            if k > 0:
                yield ''
            if sect._comment:
                yield f'# {sect._comment}'
            yield f'[{sectname}]'
            items = sorted(sect, key=lambda tup: tup[0])
            for itemname, item in items:
                yield f'# {item._comment}'
                yield item.item_def

    return u'\n'.join(_gen_dump(cfg))
