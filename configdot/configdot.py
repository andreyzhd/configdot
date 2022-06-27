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
# whitespace, alphanumeric item name (at least 1 char), whitespace, equals sign,
# item value (may be anything at this point) matched non-greedily so it doesn't
# match the trailing whitespace, trailing whitespace
RE_ITEM_DEF = r'\s*(\w+)\s*=\s*(.*?)\s*$'
# whitespace, 1 or more ['s, section name, 1 or more ]'s, whitespace, end of line
RE_SECTION_HEADER = r'\s*(\[+)([\w-]+)(\]+)\s*$'


def _simple_match(r, s):
    """Match regex r against string s"""
    return bool(re.match(r, s))


def _is_comment(s):
    """Check if s is a comment"""
    return _simple_match(RE_COMMENT, s)


def _is_whitespace(s):
    """Check if s is whitespace only"""
    return _simple_match(RE_WHITESPACE, s)


def _parse_item_def(s):
    """Match (possibly partial) config item definition.

    Return varname, val tuple if successful"""
    m = re.match(RE_ITEM_DEF, s)
    if m:
        varname, val = m.group(1), m.group(2)
        return varname, val


def _parse_section_header(s):
    """Match section or subsection (or subsubsection etc.) header.

    Headers are written e.g. [header] or [[header]] etc. where the number of
    brackets indicates the level of nesting (here 1 and 2, respectively)
    Returns a tuple of (sec_name, sec_level).
    """
    m = re.match(RE_SECTION_HEADER, s)
    if m:
        opening, closing = m.group(1), m.group(3)
        if (sec_level := len(opening)) == len(closing):
            return m.group(2), sec_level


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
        s = '<ConfigContainer |'
        items = [name for name, it in self._items.items() if isinstance(it, ConfigItem)]
        if items:
            s += ' items: '
            s += ', '.join(f"'{key}'" for key in items)
        sections = [
            name for name, it in self._items.items() if isinstance(it, ConfigContainer)
        ]
        if sections:
            if items:
                s += ','
            s += ' sections: '
            s += ', '.join(f"'{key}'" for key in sections)
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
    """
    comment_lines = list()  # comments for current variable
    current_section = None
    current_item_name = None
    current_def_lines = list()  # definition lines for current variable
    config = ConfigContainer()
    # mapping of section -> section level; 0 is the root (the config object) 1
    # is a section, 2 is a subsection, etc.
    sections = {config: 0}

    # loop through the lines
    # every line is either: comment, section header, variable definition,
    # continuation of variable definition, or whitespace
    for lnum, li in enumerate(lines, 1):

        if (sec_def := _parse_section_header(li)) is not None:
            secname, sec_level = sec_def
            if current_item_name:  # did not finish previous definition
                raise ValueError(f'could not evaluate definition at line {lnum}')
            comment = ' '.join(comment_lines)
            current_section = ConfigContainer(comment=comment)
            sections[current_section] = sec_level
            parents = [sec for sec, level in sections.items() if level == sec_level - 1]
            if not parents:
                raise ValueError(f'subsection outside a parent section at line {lnum}')
            else:
                latest_parent = parents[-1]
            setattr(latest_parent, secname, current_section)
            comment_lines = list()

        elif _is_comment(li):
            if current_item_name:
                raise ValueError(f'could not evaluate definition at line {lnum}')
            m = re.match(RE_COMMENT, li)
            cmnt = m.group(1)
            comment_lines.append(cmnt)

        elif _is_whitespace(li):
            if current_item_name:
                raise ValueError(f'could not evaluate definition at line {lnum}')

        # new item definition
        elif (item_def := _parse_item_def(li)) is not None:
            item_name, val = item_def
            if current_item_name:
                raise ValueError(f'could not evaluate definition at line {lnum}')
            elif not current_section:
                raise ValueError(f'item definition outside of a section on line {lnum}')
            elif item_name in current_section:
                raise ValueError(f'duplicate definition on line {lnum}')
            try:
                val_eval = ast.literal_eval(val)
                # if eval is successful, record the variable
                comment = ' '.join(comment_lines)
                item = ConfigItem(comment=comment, name=item_name, value=val_eval)
                setattr(current_section, item_name, item)
                comment_lines = list()
                current_def_lines = list()
                current_item_name = None
            except (ValueError, SyntaxError):  # eval failed, continued def?
                current_item_name = item_name
                current_def_lines.append(val)
                continue

        else:  # if none of the above, must be a continuation or syntax error
            if current_item_name:
                current_def_lines.append(li.strip())
            else:
                raise ValueError(f'syntax error at line {lnum}: {li}')
            # try to finish the def
            try:
                val_new = ''.join(current_def_lines)
                val_eval = ast.literal_eval(val_new)
                comment = ' '.join(comment_lines)
                item = ConfigItem(
                    comment=comment, name=current_item_name, value=val_eval
                )
                setattr(current_section, current_item_name, item)
                comment_lines = list()
                current_def_lines = list()
                current_item_name = None
            except (ValueError, SyntaxError):  # cannot finish def (yet)
                continue

    if current_item_name:  # we got to the end, but did not finish a definition
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


def _dump_section(sec):
    """Dump a ConfigContainer in text format.

    Yields lines that should reproduce the .INI used to produce the container
    (however, multiline comments are not preserved)
    """
    for item_name, item in sec:
        if isinstance(item, ConfigContainer):
            yield f'[{item_name}]'
            yield from _dump_section(item)
        elif isinstance(item, ConfigItem):
            yield item.item_def


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
    return '\n'.join(_dump_section(cfg))
