import pandas as pd

from replay.models.extensions.ann.index_inferers.base_inferer import IndexInferer
from replay.models.extensions.ann.index_inferers.utils import get_csr_matrix
from replay.models.extensions.ann.utils import create_nmslib_index_instance
from replay.utils import PYSPARK_AVAILABLE, PandasDataFrame, SparkDataFrame
from replay.utils.session_handler import State

if PYSPARK_AVAILABLE:
    from pyspark.sql.pandas.functions import pandas_udf


# pylint: disable=too-few-public-methods
class NmslibIndexInferer(IndexInferer):
    """Nmslib index inferer without filter seen items. Infers nmslib hnsw index."""

    def infer(
        self, vectors: SparkDataFrame, features_col: str, k: int
    ) -> SparkDataFrame:
        _index_store = self.index_store
        index_params = self.index_params

        index_store_broadcast = State().session.sparkContext.broadcast(
            _index_store
        )

        @pandas_udf(self.udf_return_type)
        def infer_index_udf(
            user_idx: pd.Series,
            vector_items: pd.Series,
            vector_ratings: pd.Series,
        ) -> PandasDataFrame:
            index_store = index_store_broadcast.value
            index = index_store.load_index(
                init_index=lambda: create_nmslib_index_instance(index_params),
                load_index=lambda index, path: index.loadIndex(
                    path, load_data=True
                ),
                configure_index=lambda index: index.setQueryTimeParams(
                    {"efSearch": index_params.ef_s}
                )
                if index_params.ef_s
                else None,
            )

            user_vectors = get_csr_matrix(
                user_idx, vector_items, vector_ratings
            )
            neighbours = index.knnQueryBatch(
                user_vectors[user_idx.values, :], k=k, num_threads=1
            )

            pd_res = PandasDataFrame(neighbours, columns=["item_idx", "distance"])

            # pd_res looks like
            # item_idx       distances
            # [1, 2, 3, ...] [-0.5, -0.3, -0.1, ...]
            # [1, 3, 4, ...] [-0.1, -0.8, -0.2, ...]

            return pd_res

        cols = ["user_idx", "vector_items", "vector_ratings"]

        res = vectors.select(
            "user_idx",
            infer_index_udf(*cols).alias("neighbours"),
        )

        res = self._unpack_infer_struct(res)

        return res
