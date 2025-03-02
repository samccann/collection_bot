import logging
import re

ACTION_PLUGIN_PATTERN = re.compile(r'(?:lib/ansible|plugins)/action')
MODULE_PATTERN = re.compile(r'(?:lib/ansible|plugins)/modules')
MODULE_UTIL_PATTERN = re.compile(r'(?:lib/ansible|plugins)/module_utils')

# Known possible Ansible plugin types
PLUGIN_TYPES = [
    'action',
    'become',
    'cache',
    'callback',
    'cliconf',
    'connection',
    'doc_fragments',
    'filter',
    'httpapi',
    'inventory',
    'lookup',
    'modules',
    'module_utils',
    'netconf',
    'shell',
    'strategy',
    'terminal',
    'test',
    'vars'
]
PLUGIN_PATTERN = re.compile(r'(?:lib/ansible|plugins)/(?:%s)' % '|'.join(PLUGIN_TYPES))


def get_component_match_facts(iw, component_matcher, valid_labels):
    '''High level abstraction for matching components to repo files'''

    # These should never return a match
    BLACKLIST_COMPONENTS = [
        'core', 'ansible'
    ]

    cmeta = {
        'is_collection': False,
        'is_module': False,
        'is_action_plugin': False,
        'is_new_module': False,
        'is_new_directory': False,
        'is_module_util': False,
        'is_plugin': False,
        'is_new_plugin': False,
        'is_core': False,
        'is_multi_module': False,
        'module_match': None,
        'component': None,
        'component_name': [],
        'component_match_strategy': None,
        'component_matches': [],
        'component_filenames': [],
        'component_labels': [],
        'component_maintainers': [],
        'component_namespace_maintainers': [],
        'component_notifiers': [],
        'component_support': [],
        'component_scm': None,
        'component_collection': None,
        'needs_component_message': False,
    }

    skip_matching = False
    if iw.is_issue():
        t_component = iw.template_data.get('component name')
        cmeta['component_name'] = t_component

        if not t_component or t_component.lower() in BLACKLIST_COMPONENTS:
            if t_component is None:
                logging.debug('component is None')
            elif t_component.lower() in BLACKLIST_COMPONENTS:
                logging.debug(f'{t_component} is a blacklisted component')
            skip_matching = True

    # Check if this PR is screwed up in some way
    cmeta.update(get_pr_quality_facts(iw))
    if cmeta['is_bad_pr']:
        return cmeta

    if skip_matching:
        # we still need to proceed to process the component commands
        CM_MATCHES = []
    else:
        # Try to match against something known ...
        CM_MATCHES = component_matcher.match(iw)
        cmeta['component_match_strategy'] = component_matcher.strategies

    # Reconcile with component commands ...
    if iw.is_issue():
        _CM_MATCHES = CM_MATCHES[:]
        CM_MATCHES = reconcile_component_commands(iw, component_matcher, CM_MATCHES)
        if _CM_MATCHES != CM_MATCHES:
            cmeta['component_match_strategy'] = ['component_command']

    # sort so that the filenames show up in the alphabetical/consisten order
    CM_MATCHES = sorted(CM_MATCHES, key=lambda k: k['repo_filename'])

    cmeta['component_matches'] = CM_MATCHES[:]
    cmeta['component_filenames'] = [x['repo_filename'] for x in CM_MATCHES]

    # Reduce the set of labels
    for x in CM_MATCHES:
        for y in x['labels']:
            if y in valid_labels and y not in cmeta['component_labels']:
                cmeta['component_labels'].append(y)

    # Need to reduce the set support field ...
    cmeta['component_support'] = sorted({x['support'] for x in CM_MATCHES})
    if cmeta['component_support'] != ['community']:
        cmeta['is_core'] = True

    # Reduce the set of maintainers
    for x in CM_MATCHES:
        for y in x['maintainers']:
            if y not in cmeta['component_maintainers']:
                cmeta['component_maintainers'].append(y)

    # Reduce the set of namespace maintainers
    for x in CM_MATCHES:
        for y in x.get('namespace_maintainers', []):
            if y not in cmeta['component_namespace_maintainers']:
                cmeta['component_namespace_maintainers'].append(y)

    # Reduce the set of notifiers
    for x in CM_MATCHES:
        for y in x['notify']:
            if y not in cmeta['component_notifiers']:
                cmeta['component_notifiers'].append(y)

    # Get rid of those who wish to be ignored
    for x in CM_MATCHES:
        for y in x['ignore']:
            if y in cmeta['component_maintainers']:
                cmeta['component_maintainers'].remove(y)
            if y in cmeta['component_notifiers']:
                cmeta['component_notifiers'].remove(y)

    # is it a module ... or two?
    module_matches = [x for x in CM_MATCHES if MODULE_PATTERN.match(x['repo_filename'])]
    if module_matches:
        cmeta['is_module'] = True

        if len(module_matches) > 1:
            cmeta['is_multi_module'] = True

        cmeta['module_match'] = module_matches

    # is it a plugin?
    if [x for x in CM_MATCHES if PLUGIN_PATTERN.match(x['repo_filename'])]:
        cmeta['is_plugin'] = True

    # is it an action plugin?
    if [x for x in CM_MATCHES if ACTION_PLUGIN_PATTERN.match(x['repo_filename'])]:
        cmeta['is_action_plugin'] = True

    # is it a module util?
    if [x for x in CM_MATCHES if MODULE_UTIL_PATTERN.match(x['repo_filename'])]:
        cmeta['is_module_util'] = True

    if iw.is_pullrequest():
        if iw.new_modules:
            cmeta['is_new_module'] = True
            cmeta['is_new_plugin'] = True

        # https://github.com/ansible/ansibullbot/issues/684
        if iw.new_files:
            for x in iw.new_files:
                if '/plugins/' in x:
                    cmeta['is_new_plugin'] = True

    # is it a collection?
    if [x for x in CM_MATCHES if x['repo_filename'].startswith('collection:')]:
        cmeta['is_collection'] = True
        cmeta['component_collection'] = []
        for comp in [x for x in CM_MATCHES if x['repo_filename'].startswith('collection:')]:
            fqcn = comp['repo_filename'].split(':')[1]
            cmeta['component_collection'].append(fqcn)

    # welcome message to indicate which files the bot matched
    if iw.is_issue():

        # We only want to add this comment in two scenarios:
        #   * no other comments have been made yet
        #   * the last comment had different files

        if len(iw.comments) == 0:
            cmeta['needs_component_message'] = True

        else:
            bpcs = iw.history.get_boilerplate_comments(dates=True, content=True)
            bpcs = [x for x in bpcs if x[1] == 'components_banner']

            # was the last list of files correct?
            if bpcs:
                lbpc = bpcs[-1]
                lbpc = lbpc[-1]
                _filenames = []
                for line in lbpc.split('\n'):
                    if line.startswith('*'):
                        # escaped lines screw up the regex here
                        line = line.replace('`', '')
                        parts = line.split()
                        try:
                            m = re.match(r'\[(\S+)\].*', parts[1])
                        except IndexError:
                            continue
                        if m:
                            _filenames.append(m.group(1))
                        else:
                            # https://github.com/ansible/ansibullbot/pull/1425/
                            if 'None' not in parts[1]:
                                _filenames.append(parts[1])
                _filenames = sorted(set(_filenames))
                expected = sorted({x['repo_filename'] for x in CM_MATCHES})
                if _filenames != expected:
                    cmeta['needs_component_message'] = True

    return cmeta


def reconcile_component_commands(iw, component_matcher, CM_MATCHES):
    """Allow components to be set by bot commands"""
    component_commands = iw.history.get_component_commands()
    component_filenames = [x['repo_filename'] for x in CM_MATCHES]

    for ccx in component_commands:

        if '\n' in ccx['body']:
            lines = ccx['body'].split('\n')
            lines = [x.strip() for x in lines if x.strip()]
        else:
            lines = [ccx['body'].strip()]

        # keep track if files are reset in the same comment
        cleared = False

        for line in lines:

            if not line.strip().startswith('!component'):
                continue

            # !component [action][filename]
            try:
                filen = line.split()[1]
            except IndexError:
                filen = line.replace('!component', '')

            # https://github.com/ansible/ansible/issues/37494#issuecomment-373548008
            if not filen:
                continue

            action = filen[0]
            filen = filen[1:].strip()

            if action == '+' and filen not in component_filenames:
                component_filenames.append(filen)
            elif action == '-' and filen in component_filenames:
                component_filenames.remove(filen)
            elif action == '=':
                # possibly unintuitive but multiple ='s in the same comment
                # should initially clear the set and then become additive.
                if not cleared:
                    component_filenames = [filen]
                else:
                    component_filenames.append(filen)
                cleared = True

    CM_MATCHES = component_matcher.match_components('', '', '', files=component_filenames)

    return CM_MATCHES


def get_pr_quality_facts(issuewrapper):

    '''Use arbitrary counts to prevent notification+label storms'''

    iw = issuewrapper

    qmeta = {
        'is_bad_pr': False,
        'is_bad_pr_reason': list(),
        'is_empty_pr': False
    }

    if not iw.is_pullrequest():
        return qmeta

    for f in iw.files:
        if f.startswith('lib/ansible/modules/core') or \
                f.startswith('lib/ansible/modules/extras'):
            qmeta['is_bad_pr'] = True

    # https://github.com/ansible/ansibullbot/issues/534
    try:
        if len(iw.files) == 0:
            qmeta['is_bad_pr'] = True
            qmeta['is_empty_pr'] = True
    except:
        pass

    try:
        if len(iw.files) > 50:
            qmeta['is_bad_pr'] = True
            qmeta['is_bad_pr_reason'].append('More than 50 changed files.')

        if len(iw.commits) > 50:
            qmeta['is_bad_pr'] = True
            qmeta['is_bad_pr_reason'].append('More than 50 commits.')
    except:
        # bypass exceptions for unit tests
        pass

    return qmeta
