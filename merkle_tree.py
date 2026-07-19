import hashlib
from typing import List, Dict

class MerkleTree:
    def __init__(self, leaves: List[str]):
        # Hash each leaf to form the base level
        self.leaves = [self._hash(leaf) for leaf in leaves]
        self.levels = []
        if self.leaves:
            self.levels.append(self.leaves)
            self._build_tree()
        else:
            self.levels.append([self._hash("")])

    def _hash(self, val: str) -> str:
        return hashlib.sha256(val.encode("utf-8")).hexdigest()

    def _build_tree(self):
        current_level = self.leaves
        while len(current_level) > 1:
            next_level = []
            for i in range(0, len(current_level), 2):
                left = current_level[i]
                # If there's no right sibling, duplicate the left node
                right = current_level[i + 1] if i + 1 < len(current_level) else left
                combined = left + right
                next_level.append(self._hash(combined))
            self.levels.append(next_level)
            current_level = next_level

    def get_root(self) -> str:
        return self.levels[-1][0] if self.levels else self._hash("")

    def get_proof(self, index: int) -> List[Dict[str, str]]:
        """Generates a Merkle proof for a leaf index showing siblings up to the root."""
        proof = []
        if not self.leaves or index < 0 or index >= len(self.leaves):
            return proof

        current_idx = index
        for level in self.levels[:-1]:  # Exclude root level
            is_right = current_idx % 2 == 1
            sibling_idx = current_idx - 1 if is_right else current_idx + 1
            
            # Sibling could be out of bounds for odd levels, duplicate current_idx node
            if sibling_idx < len(level):
                sibling_hash = level[sibling_idx]
            else:
                sibling_hash = level[current_idx]
                
            proof.append({
                "hash": sibling_hash,
                "position": "left" if is_right else "right"
            })
            current_idx = current_idx // 2
        return proof

    @staticmethod
    def verify_proof(leaf: str, proof: List[Dict[str, str]], root: str) -> bool:
        """Verifies a Merkle proof against a target root."""
        current_hash = hashlib.sha256(leaf.encode("utf-8")).hexdigest()
        for step in proof:
            sibling = step["hash"]
            position = step["position"]
            if position == "left":
                combined = sibling + current_hash
            else:
                combined = current_hash + sibling
            current_hash = hashlib.sha256(combined.encode("utf-8")).hexdigest()
        return current_hash == root
