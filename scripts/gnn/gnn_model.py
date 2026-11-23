"""G2: edge-centric GNN for Blue Vector (Deliverable 5).

Input: node features [N, 6], edge features [E, 11], edge_index [2, E].
Output: per-edge predictions [E, 4] (oil_volume_bbl, tanker_transits,
utilization, delay_days), all >= 0, plus a graph embedding [8].

Plain PyTorch only. Messages go along edge_index (row 0 = from, row 1 = to)
and are summed at the receiving node.
"""
import torch
from torch import nn

N_NODE_FEATURES = 6
N_EDGE_FEATURES = 11
N_TARGETS = 4
EMB_DIM = 8
THROTTLE_COL = 7  # index of throttle_factor in the 11 edge features (see G1)
TARGETS = ["oil_volume_bbl", "tanker_transits", "utilization", "delay_days"]
# Targets that must be exactly zero when an edge is closed.
CLOSED_ZERO = [0, 1]  # oil_volume_bbl, tanker_transits


def mlp(d_in, d_hidden, d_out):
    return nn.Sequential(nn.Linear(d_in, d_hidden), nn.ReLU(), nn.Linear(d_hidden, d_out))


class EdgeGNN(nn.Module):
    def __init__(self, node_in=N_NODE_FEATURES, edge_in=N_EDGE_FEATURES, hidden=64,
                 layers=3, emb_dim=EMB_DIM, throttle_col=THROTTLE_COL):
        super().__init__()
        self.throttle_col = throttle_col
        self.node_enc = mlp(node_in, hidden, hidden)
        self.edge_enc = mlp(edge_in, hidden, hidden)
        self.node_lin = nn.ModuleList(nn.Linear(hidden, hidden) for _ in range(layers))
        self.edge_upd = nn.ModuleList(mlp(3 * hidden, hidden, hidden) for _ in range(layers))
        self.node_norm = nn.ModuleList(nn.LayerNorm(hidden) for _ in range(layers))
        self.edge_norm = nn.ModuleList(nn.LayerNorm(hidden) for _ in range(layers))
        self.edge_head = nn.Sequential(nn.Linear(hidden, hidden), nn.ReLU(),
                                       nn.Linear(hidden, N_TARGETS), nn.Softplus())
        self.emb_head = nn.Linear(2 * hidden, emb_dim)

    def forward(self, x_node, x_edge, edge_index):
        src, dst = edge_index
        h_node = self.node_enc(x_node)
        h_edge = self.edge_enc(x_edge)
        for lin, upd, nn_, en_ in zip(self.node_lin, self.edge_upd, self.node_norm, self.edge_norm):
            # Each CSV row is a physical corridor carrying both travel directions.
            agg = torch.zeros_like(h_node).index_add_(0, dst, h_edge).index_add_(0, src, h_edge)
            h_node = nn_(h_node + torch.relu(lin(agg)))
            h_edge = en_(h_edge + upd(torch.cat([h_node[src], h_node[dst], h_edge], dim=-1)))

        pred = self.edge_head(h_edge)
        open_mask = (x_edge[:, self.throttle_col] > 0).to(pred.dtype)  # 0 where closed
        scale = torch.ones_like(pred)
        scale[:, CLOSED_ZERO] = open_mask.unsqueeze(-1)
        pred = pred * scale

        pooled = torch.cat([h_edge.mean(dim=0), h_edge.max(dim=0).values])
        return pred, self.emb_head(pooled)


if __name__ == "__main__":
    # Smoke test of the G2 acceptance criteria on a random 142-node / 279-edge graph.
    torch.manual_seed(0)
    N, E = 142, 279
    ei = torch.stack([torch.randint(0, N, (E,)), torch.randint(0, N, (E,))])
    xn, xe = torch.randn(N, N_NODE_FEATURES), torch.rand(E, N_EDGE_FEATURES)
    xe[:, THROTTLE_COL] = 1.0
    closed = torch.tensor([3, 50, 200])
    xe[closed, THROTTLE_COL] = 0.0

    model = EdgeGNN()
    pred, emb = model(xn, xe, ei)
    assert pred.shape == (E, N_TARGETS) and emb.shape == (EMB_DIM,)
    assert (pred >= 0).all() and torch.isfinite(pred).all() and torch.isfinite(emb).all()
    assert (pred[closed][:, CLOSED_ZERO] == 0).all()
    assert (pred[0, CLOSED_ZERO] > 0).all()
    (pred.sum() + emb.sum()).backward()
    assert all(p.grad is not None for p in model.parameters())
    print("OK", tuple(pred.shape), tuple(emb.shape), "params:", sum(p.numel() for p in model.parameters()))
