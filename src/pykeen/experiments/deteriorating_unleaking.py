# -*- coding: utf-8 -*-

"""Run the deterioration experiments."""

import itertools as itt
import logging
import os
import pickle
import time
from functools import partial
from typing import Mapping, Type

import click
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns
from tqdm.autonotebook import tqdm

import pykeen.triples.triples_factory
from pykeen.constants import PYKEEN_HOME
from pykeen.datasets import CoDExLarge, CoDExMedium, CoDExSmall, FB15k237, WN18RR, get_dataset
from pykeen.datasets.base import DataSet
from pykeen.triples.remix import cleanup_dataset, dataset_splits_distance, deteriorate_dataset, starmap_ctx

__all__ = [
    'deteriorating',
]

DETERIORATION_DIR = os.path.join(PYKEEN_HOME, 'experiments', 'deterleak')
os.makedirs(DETERIORATION_DIR, exist_ok=True)

JITTER = 0.1

NO_RANDOM: Mapping[Type[DataSet], float] = {
    WN18RR: 0.6,
    CoDExSmall: 0.90,
    CoDExMedium: 0.85,
    CoDExLarge: 0.85,
    FB15k237: 0.5,
}


def helper(dataset: str, use_multiprocessing: bool = True) -> pd.DataFrame:
    dataset = get_dataset(dataset=dataset)
    dataset_name = dataset.__class__.__name__

    replicates = 4
    percentages = [0.001, 0.002, 0.005, 0.01, 0.02, 0.05] + [i / 10 for i in range(1, 10)] + [0.95, 0.99, 0.999]
    randomize_cleanup_options = [True, False]

    directory = os.path.join(DETERIORATION_DIR, dataset.get_normalized_name())
    results_directory = os.path.join(directory, 'results')
    os.makedirs(results_directory, exist_ok=True)

    it = tqdm(
        itt.product(
            percentages,
            randomize_cleanup_options,
            range(1, 1 + replicates),
        ),
        total=len(percentages) * replicates * len(randomize_cleanup_options),
        desc=f'Deteriorating {dataset_name}',
        disable=use_multiprocessing,  # if multiprocessing is on, there's no point to this
    )
    f = partial(_help_loop, results_directory=results_directory, dataset=dataset)

    if use_multiprocessing:
        tqdm.write(f'Running {dataset} (training={dataset.training.num_triples})')
    with starmap_ctx(use_multiprocessing=use_multiprocessing) as ctx:
        rows = list(ctx.starmap(f, it))

    rows = [row for row in rows if row is not None]
    df = pd.DataFrame(rows, columns=[
        'dataset', 'cleanup', 'percentage', 'trial', 'distance',
        'deteriorate_time', 'cleanup_time',
    ])
    path = os.path.join(directory, 'results.tsv')
    df.to_csv(path, sep='\t', index=False)

    for y in ['distance', 'deteriorate_time', 'cleanup_time']:
        fig, ax = plt.subplots(1, 1)
        sns.scatterplot(
            data=df,
            x='percentage', y=y,
            ax=ax, hue='cleanup', alpha=.7, x_jitter=JITTER, y_jitter=JITTER,
        )
        summary_path = os.path.join(directory, f'{y}_summary.png')
        plt.title(f'Deterioration Study of {dataset_name}')
        plt.xlabel('Percentage Training Deterioration')
        plt.savefig(summary_path, dpi=300)
        plt.close(fig)

    return df


def _help_loop(ratio: float,  randomize_cleanup: bool, replicate: int, *, dataset: DataSet, results_directory: str):
    if dataset.__class__ in NO_RANDOM and randomize_cleanup and NO_RANDOM[dataset.__class__] < ratio:
        print('skipped', dataset, ratio, replicate)
        return

    rct = 'random' if randomize_cleanup else 'deterministic'
    path = os.path.join(results_directory, f'{int(ratio * 1000):03}_{replicate:03}_{rct}.pkl')

    if os.path.exists(path):
        with open(path, 'rb') as file:
            nd, deteriorate_time, cleanup_time = pickle.load(file)
    else:
        random_state = np.random.RandomState(replicate)
        s = time.time()
        deteriorated_dataset = deteriorate_dataset(dataset, n=ratio, random_state=random_state)
        deteriorate_time = time.time() - s
        s = time.time()
        nd = cleanup_dataset(deteriorated_dataset, random_state=random_state, randomize_cleanup=randomize_cleanup)
        cleanup_time = time.time() - s
        with open(path, 'wb') as file:
            pickle.dump((nd, deteriorate_time, cleanup_time), file, protocol=pickle.HIGHEST_PROTOCOL)

    nd_distance = dataset_splits_distance(dataset, nd)
    return (
        dataset.get_normalized_name(),
        rct,
        ratio,
        replicate,
        nd_distance,
        deteriorate_time,
        cleanup_time,
    )


@click.command()
def deteriorating():
    """Run the deterioration experiments."""
    logging.getLogger('pykeen.evaluation.evaluator').setLevel(logging.ERROR)
    logging.getLogger('pykeen.stoppers.early_stopping').setLevel(logging.ERROR)
    pykeen.triples.triples_factory.logger.setLevel(logging.ERROR)
    logging.getLogger('pykeen.models.cli').setLevel(logging.ERROR)

    datasets = [
        'nations',
        'kinships',
        'umls',
        'codexsmall',
        'codexmedium',
        'wn18rr',
        'FB15k237',
    ]
    dfs = [helper(dataset) for dataset in datasets]
    df = pd.concat(dfs)
    df.to_csv(os.path.join(DETERIORATION_DIR, 'results.tsv'), sep='\t', index=False)

    for y, sharey in [
        ('distance', True),
        ('deteriorate_time', False),
        ('cleanup_time', False),
    ]:
        summary_path = os.path.join(DETERIORATION_DIR, f'{y}_summary.png')
        g = sns.FacetGrid(data=df, col="dataset", hue='cleanup', col_wrap=3, sharey=sharey)
        g.map(sns.scatterplot, "percentage", y, alpha=.7, x_jitter=JITTER, y_jitter=JITTER)
        g.add_legend()
        g.savefig(summary_path, dpi=300)
        plt.close(g.fig)


if __name__ == '__main__':
    deteriorating()
