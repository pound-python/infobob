from setuptools import setup

install_requires = [
    # Pin deps for now, to be upgraded after tests are much expanded.
    'Genshi==0.7',
    'lxml==3.6.0',
    'Pygments==1.4',
    'python-dateutil==2.5.3',
    'Twisted[tls]==16.1.1',
    'klein',
    'treq',
    'attrs',
]

extras_require = {
    'testing': [
        'coverage',
        'ddt',
    ],
}

setup(
    name='infobob',
    version='0.1.0-dev',
    author='habnabit',
    author_email='_@habnab.it',
    maintainer='Colin Dunklau',
    maintainer_email='colin.dunklau@gmail.com',
    packages=['infobob', 'infobob.tests', 'twisted.plugins'],
    include_package_data=True,
    install_requires=install_requires,
    extras_require=extras_require,
)
