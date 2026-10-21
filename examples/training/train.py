"""Fit a polynomial to datasets/points by stochastic gradient descent, one run per configuration and seed."""

import random

import hydra
from omegaconf import OmegaConf

import cairn


def errors(weights, rows):
    for row in rows:
        powers = [row["x"] ** p for p in range(len(weights))]
        yield sum(w * x for w, x in zip(weights, powers)) - row["y"], powers


@hydra.main(config_path="conf", config_name="config", version_base="1.3")
def main(cfg):
    name = f"fit/{cfg.model.name}-lr{cfg.learning_rate}/seed{cfg.seed}"
    with cairn.init(name, config=OmegaConf.to_container(cfg, resolve=True)):
        rows = cairn.use(cfg.dataset, role="training-data").value.load()
        random.seed(cfg.seed)
        weights = [random.gauss(0, 0.1) for _ in range(cfg.model.degree + 1)]
        for step in range(cfg.steps):
            batch = random.sample(rows, cfg.batch_size)
            loss, gradient = 0.0, [0.0] * len(weights)
            for error, powers in errors(weights, batch):
                loss += error**2 / len(batch)
                gradient = [g + 2 * error * x / len(batch) for g, x in zip(gradient, powers)]
            weights = [w - cfg.learning_rate * g for w, g in zip(weights, gradient)]
            cairn.log(step=step, loss=loss)
        loss = sum(error**2 for error, _ in errors(weights, rows)) / len(rows)
        cairn.finish(value={"loss": loss, "weights": weights})


if __name__ == "__main__":
    main()
