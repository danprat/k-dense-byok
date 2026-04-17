"""AnnData (.h5ad) preview helpers.

Lazy-imports ``anndata``/``h5py``/``matplotlib``/``scipy`` so the backend
boots even on machines that haven't installed the single-cell stack. The
FastAPI routes in ``server.py`` translate ``ImportError`` into a 503 so
the UI can show a friendly "install these deps" message.

Two pieces of functionality:

1. ``summarize_h5ad(path)`` — opens the file in ``backed="r"`` mode and
   returns a JSON-serialisable dict describing shapes, layers, obs/var
   column previews, and which obsm keys look like 2D embeddings.
2. ``render_embedding_png(path, key, color, cache_dir)`` — renders the
   first two columns of ``obsm[key]`` with matplotlib (Agg) at 320x320
   and caches the result on disk keyed by ``(path, mtime, key, color)``.
"""

from __future__ import annotations

import hashlib
import io
import math
from pathlib import Path
from typing import Any, Optional

_EMBEDDING_PRIORITY = ("X_umap", "X_tsne", "X_pca")
_EMBEDDING_PREFIXES = ("X_umap", "X_tsne", "X_pca", "X_draw_graph", "X_spatial")
_MAX_POINTS = 20_000
_COL_PREVIEW_LIMIT = 200  # obs/var columns beyond this are summarised only
_CATEGORICAL_TOP_N = 5


class AnnDataDepsMissing(RuntimeError):
    """Raised when the anndata/h5py/matplotlib stack isn't installed."""


def _import_anndata():
    try:
        import anndata as ad  # noqa: WPS433

        return ad
    except ImportError as exc:  # pragma: no cover - env-dependent
        raise AnnDataDepsMissing(
            "anndata is not installed. Install with: "
            "uv add anndata h5py matplotlib scipy"
        ) from exc


def _import_matplotlib():
    try:
        import matplotlib  # noqa: WPS433

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt  # noqa: WPS433

        return matplotlib, plt
    except ImportError as exc:  # pragma: no cover - env-dependent
        raise AnnDataDepsMissing(
            "matplotlib is not installed. Install with: "
            "uv add anndata h5py matplotlib scipy"
        ) from exc


def _jsonable(value: Any) -> Any:
    """Coerce numpy/pandas scalars into plain JSON types."""
    if value is None:
        return None
    try:
        import numpy as np  # noqa: WPS433
    except ImportError:
        np = None  # type: ignore[assignment]
    if np is not None:
        if isinstance(value, np.generic):
            item = value.item()
            if isinstance(item, float) and (math.isnan(item) or math.isinf(item)):
                return None
            return item
        if isinstance(value, np.ndarray):
            return [_jsonable(v) for v in value.tolist()]
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def _column_stats(series) -> dict:
    """Describe a pandas Series for the obs/var preview table."""
    import numpy as np  # noqa: WPS433
    import pandas as pd  # noqa: WPS433

    dtype = str(series.dtype)
    n = len(series)

    if isinstance(series.dtype, pd.CategoricalDtype) or dtype == "category":
        cats = list(series.cat.categories[:_CATEGORICAL_TOP_N])
        vc = series.value_counts(dropna=True).head(_CATEGORICAL_TOP_N)
        return {
            "dtype": "categorical",
            "n_unique": int(series.cat.categories.size),
            "categories": [_jsonable(c) for c in cats],
            "top": [
                {"value": _jsonable(idx), "count": int(cnt)}
                for idx, cnt in vc.items()
            ],
        }

    if pd.api.types.is_numeric_dtype(series):
        arr = series.to_numpy()
        arr = arr[~pd.isna(arr)] if arr.size else arr
        if arr.size == 0:
            return {"dtype": dtype, "n_unique": 0}
        return {
            "dtype": dtype,
            "n_unique": int(pd.Series(arr).nunique()),
            "min": _jsonable(float(np.min(arr))),
            "max": _jsonable(float(np.max(arr))),
            "mean": _jsonable(float(np.mean(arr))),
        }

    if pd.api.types.is_bool_dtype(series):
        return {
            "dtype": "bool",
            "n_unique": int(series.nunique(dropna=True)),
            "n_true": int(series.sum()),
            "n_false": int(n - series.sum()),
        }

    vc = series.astype(str).value_counts(dropna=True).head(_CATEGORICAL_TOP_N)
    return {
        "dtype": dtype,
        "n_unique": int(series.nunique(dropna=True)),
        "top": [
            {"value": str(idx), "count": int(cnt)}
            for idx, cnt in vc.items()
        ],
    }


def _describe_dataframe(df, limit: int = _COL_PREVIEW_LIMIT) -> list[dict]:
    cols = list(df.columns)
    truncated = len(cols) > limit
    selected = cols[:limit] if truncated else cols
    out: list[dict] = []
    for name in selected:
        try:
            stats = _column_stats(df[name])
        except Exception as exc:  # noqa: BLE001 - robust to weird dtypes
            stats = {"dtype": "unknown", "error": str(exc)}
        out.append({"name": str(name), **stats})
    return out


def _matrix_info(mat) -> dict:
    """Shape/dtype/sparsity for adata.X or a layer."""
    info: dict = {}
    shape = getattr(mat, "shape", None)
    if shape is not None:
        info["shape"] = [int(s) for s in shape]
    dtype = getattr(mat, "dtype", None)
    if dtype is not None:
        info["dtype"] = str(dtype)
    try:
        import scipy.sparse as sp  # noqa: WPS433

        info["sparse"] = bool(sp.issparse(mat))
    except ImportError:
        info["sparse"] = False
    return info


def _list_embeddings(obsm_keys: list[str], obsm) -> list[dict]:
    out: list[dict] = []
    for key in obsm_keys:
        try:
            arr = obsm[key]
            shape = getattr(arr, "shape", None)
            if shape is None or len(shape) < 2 or shape[1] < 2:
                continue
        except Exception:  # noqa: BLE001
            continue
        is_embedding = any(
            key == prefix or key.startswith(prefix + "_") or key.startswith(prefix)
            for prefix in _EMBEDDING_PREFIXES
        )
        if not is_embedding:
            continue
        out.append({"key": key, "shape": [int(s) for s in shape]})
    return out


def _default_embedding(embeddings: list[dict]) -> Optional[str]:
    keys = {e["key"] for e in embeddings}
    for pref in _EMBEDDING_PRIORITY:
        if pref in keys:
            return pref
    return embeddings[0]["key"] if embeddings else None


def summarize_h5ad(path: Path) -> dict:
    """Open an .h5ad file backed-mode and return a JSON summary."""
    ad = _import_anndata()
    try:
        from importlib.metadata import version as _pkg_version

        anndata_version = _pkg_version("anndata")
    except Exception:  # noqa: BLE001
        anndata_version = getattr(ad, "__version__", "unknown")

    adata = ad.read_h5ad(str(path), backed="r")
    try:
        obsm_keys = list(adata.obsm.keys()) if adata.obsm is not None else []
        varm_keys = list(adata.varm.keys()) if adata.varm is not None else []
        uns_keys = list(adata.uns.keys()) if adata.uns is not None else []
        obsp_keys = list(adata.obsp.keys()) if adata.obsp is not None else []
        varp_keys = list(adata.varp.keys()) if adata.varp is not None else []

        layers: list[dict] = []
        if adata.layers is not None:
            for name in list(adata.layers.keys()):
                try:
                    layers.append({"name": name, **_matrix_info(adata.layers[name])})
                except Exception as exc:  # noqa: BLE001
                    layers.append({"name": name, "error": str(exc)})

        embeddings = _list_embeddings(obsm_keys, adata.obsm)

        try:
            x_info = _matrix_info(adata.X) if adata.X is not None else {}
        except Exception as exc:  # noqa: BLE001
            x_info = {"error": str(exc)}

        summary = {
            "n_obs": int(adata.n_obs),
            "n_vars": int(adata.n_vars),
            "X": x_info,
            "layers": layers,
            "obs_columns": _describe_dataframe(adata.obs),
            "var_columns": _describe_dataframe(adata.var),
            "obs_column_count": int(adata.obs.shape[1]),
            "var_column_count": int(adata.var.shape[1]),
            "obsm_keys": obsm_keys,
            "varm_keys": varm_keys,
            "uns_keys": [str(k) for k in uns_keys],
            "obsp_keys": obsp_keys,
            "varp_keys": varp_keys,
            "embeddings": embeddings,
            "default_embedding": _default_embedding(embeddings),
            "file_size": int(path.stat().st_size),
            "anndata_version": anndata_version,
        }
    finally:
        try:
            adata.file.close()
        except Exception:  # noqa: BLE001
            pass

    return summary


def _cache_key(path: Path, key: str, color: Optional[str]) -> str:
    mtime = path.stat().st_mtime_ns
    raw = f"{path.resolve()}|{mtime}|{key}|{color or ''}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def render_embedding_png(
    path: Path,
    key: str,
    color: Optional[str],
    cache_dir: Path,
) -> bytes:
    """Render obsm[key][:, :2] as a 320x320 PNG.

    Results are cached on disk keyed by ``(abs_path, mtime_ns, key, color)``
    so flipping between embeddings in the UI is instant.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    cached = cache_dir / f"{_cache_key(path, key, color)}.png"
    if cached.is_file():
        return cached.read_bytes()

    ad = _import_anndata()
    _, plt = _import_matplotlib()
    import numpy as np  # noqa: WPS433

    adata = ad.read_h5ad(str(path), backed="r")
    try:
        if adata.obsm is None or key not in adata.obsm:
            raise KeyError(f"obsm key not found: {key}")
        coords = np.asarray(adata.obsm[key])
        if coords.ndim < 2 or coords.shape[1] < 2:
            raise ValueError(f"obsm[{key}] is not a 2D embedding")
        xs = coords[:, 0].astype(float)
        ys = coords[:, 1].astype(float)

        color_values = None
        color_is_categorical = False
        if color:
            try:
                import pandas as pd  # noqa: WPS433

                if color in adata.obs.columns:
                    series = adata.obs[color]
                    if isinstance(series.dtype, pd.CategoricalDtype) or not pd.api.types.is_numeric_dtype(series):
                        codes, _uniques = pd.factorize(series, sort=True)
                        color_values = codes.astype(float)
                        color_is_categorical = True
                    else:
                        color_values = series.to_numpy(dtype=float)
            except Exception:  # noqa: BLE001
                color_values = None

        n = xs.shape[0]
        if n > _MAX_POINTS:
            rng = np.random.default_rng(seed=0)
            idx = rng.choice(n, size=_MAX_POINTS, replace=False)
            xs = xs[idx]
            ys = ys[idx]
            if color_values is not None:
                color_values = color_values[idx]

        fig, ax = plt.subplots(figsize=(3.2, 3.2), dpi=100)
        try:
            scatter_kwargs = {"s": 4, "linewidths": 0, "alpha": 0.7}
            if color_values is not None:
                cmap = "tab20" if color_is_categorical else "viridis"
                ax.scatter(xs, ys, c=color_values, cmap=cmap, **scatter_kwargs)
            else:
                ax.scatter(xs, ys, c="#6366f1", **scatter_kwargs)
            ax.set_xticks([])
            ax.set_yticks([])
            for spine in ax.spines.values():
                spine.set_visible(False)
            ax.set_aspect("equal", adjustable="datalim")
            fig.tight_layout(pad=0.1)

            buf = io.BytesIO()
            fig.savefig(buf, format="png", dpi=100, bbox_inches="tight", pad_inches=0.05)
        finally:
            plt.close(fig)

        data = buf.getvalue()
    finally:
        try:
            adata.file.close()
        except Exception:  # noqa: BLE001
            pass

    try:
        cached.write_bytes(data)
    except OSError:
        pass
    return data
