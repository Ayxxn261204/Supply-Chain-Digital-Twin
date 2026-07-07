"""Setup configuration for supply chain simulation package."""
from setuptools import setup, find_packages

setup(
    name="supply-chain-simulation",
    version="0.1.0",
    packages=find_packages(where="src"),
    package_dir={"": "src"},
    python_requires=">=3.8",
    install_requires=[
        "pyyaml>=6.0",
        "paho-mqtt>=1.6.1",
        "osmnx>=1.2.2",
        "networkx>=2.8",
        "matplotlib>=3.5.0",
    ],
    extras_require={
        "test": [
            "pytest>=7.4.0",
            "pytest-cov>=4.0.0",
            "pytest-mock>=3.11.0",
        ]
    },
)
