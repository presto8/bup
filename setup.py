from setuptools import setup, find_packages

setup(
    name='bup',
    version='0.1',
    python_requires='<3',
    packages=find_packages(),
    include_package_data=True,
    install_requires=[
        'pyxattr',
        'pylibacl',
        'libnacl',
    ],
    scripts=[
        "cmd/bup",
    ],
)
