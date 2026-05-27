import os
from arango import ArangoClient
from pyvis.network import Network
from app.core.config import settings
from app.core.logger import logger

class GraphDBService:
    def __init__(self):
        try:
            self.client = ArangoClient(hosts=settings.arango_url)
            self.sys_db = self.client.db("_system", username=settings.arango_user, password=settings.arango_password)
            
            # Ensure DB exists
            if not self.sys_db.has_database(settings.arango_db):
                self.sys_db.create_database(settings.arango_db)
            
            self.db = self.client.db(settings.arango_db, username=settings.arango_user, password=settings.arango_password)
            
            # Initialize collections
            self._ensure_collections()
        except Exception as e:
            logger.error(f"Failed to initialize GraphDBService: {e}")
            self.db = None

    def _ensure_collections(self):
        # Node collections
        node_collections = ["runbooks", "categories", "authors", "keywords"]
        for col in node_collections:
            if not self.db.has_collection(col):
                self.db.create_collection(col)

        # Edge collections
        edge_collections = ["belongs_to", "authored_by", "has_keyword"]
        for col in edge_collections:
            if not self.db.has_collection(col):
                self.db.create_collection(col, edge=True)
                
    def _sanitize_key(self, key: str) -> str:
        """ArangoDB keys must be alphanumeric or certain safe characters."""
        import re
        if not key:
            return "unknown"
        s = re.sub(r'[^a-zA-Z0-9_\-:.@()+,=;$!*\'%]', '_', str(key))
        return s[:254] # Max length is 254

    def add_runbook_to_graph(self, article_id: str, title: str, summary_data: dict, author: str, graph_data: dict = None):
        if not self.db:
            return

        try:
            runbooks = self.db.collection("runbooks")
            categories = self.db.collection("categories")
            authors = self.db.collection("authors")
            keywords = self.db.collection("keywords")

            belongs_to = self.db.collection("belongs_to")
            authored_by = self.db.collection("authored_by")
            has_keyword = self.db.collection("has_keyword")

            rb_key = self._sanitize_key(article_id)
            
            # 1. Insert Runbook
            runbook_doc = {
                "_key": rb_key,
                "title": title,
                "summary": summary_data.get("summary", ""),
                "purpose": summary_data.get("purpose", "")
            }
            if not runbooks.has(rb_key):
                runbooks.insert(runbook_doc)
            else:
                runbooks.update(runbook_doc)
            
            # 2. Insert Category & edge
            category = summary_data.get("category")
            if category:
                cat_key = self._sanitize_key(category)
                if not categories.has(cat_key):
                    categories.insert({"_key": cat_key, "name": category})
                
                edge_key = f"{rb_key}-{cat_key}"
                if not belongs_to.has(edge_key):
                    belongs_to.insert({
                        "_key": edge_key,
                        "_from": f"runbooks/{rb_key}",
                        "_to": f"categories/{cat_key}"
                    })

            # 3. Insert Author & edge
            if author:
                auth_key = self._sanitize_key(author)
                if not authors.has(auth_key):
                    authors.insert({"_key": auth_key, "name": author})
                
                edge_key = f"{auth_key}-{rb_key}"
                if not authored_by.has(edge_key):
                    authored_by.insert({
                        "_key": edge_key,
                        "_from": f"authors/{auth_key}",
                        "_to": f"runbooks/{rb_key}"
                    })

            # 4. Insert Keywords & edges
            kws = summary_data.get("keywords") or []
            if isinstance(kws, str):
                kws = [k.strip() for k in kws.split(",")]
            
            for kw in kws:
                kw_key = self._sanitize_key(kw)
                if not keywords.has(kw_key):
                    keywords.insert({"_key": kw_key, "name": kw})
                
                edge_key = f"{rb_key}-{kw_key}"
                if not has_keyword.has(edge_key):
                    has_keyword.insert({
                        "_key": edge_key,
                        "_from": f"runbooks/{rb_key}",
                        "_to": f"keywords/{kw_key}"
                    })
                    
            # 5. Insert LLM Generated nodes & edges
            if graph_data:
                nodes = graph_data.get("nodes", [])
                edges = graph_data.get("edges", [])

                if not self.db.has_collection("llm_nodes"):
                    self.db.create_collection("llm_nodes")
                if not self.db.has_collection("llm_edges"):
                    self.db.create_collection("llm_edges", edge=True)

                llm_nodes = self.db.collection("llm_nodes")
                llm_edges = self.db.collection("llm_edges")

                for node in nodes:
                    n_id = node.get("id")
                    if not n_id: continue
                    safe_id = self._sanitize_key(n_id)
                    n_label = node.get("label", safe_id)
                    n_type = node.get("type", "Unknown")

                    doc = {"_key": safe_id, "label": n_label, "type": n_type}
                    if not llm_nodes.has(safe_id):
                        llm_nodes.insert(doc)
                    else:
                        llm_nodes.update(doc)
                    
                    # Link runbook to the extracted node
                    rb_edge_key = f"{rb_key}-{safe_id}"
                    if not llm_edges.has(rb_edge_key):
                        llm_edges.insert({
                            "_key": rb_edge_key,
                            "_from": f"runbooks/{rb_key}",
                            "_to": f"llm_nodes/{safe_id}",
                            "relationship": "mentions"
                        })

                for edge in edges:
                    f_id = edge.get("from")
                    t_id = edge.get("to")
                    rel = edge.get("relationship", "related_to")

                    if not f_id or not t_id: continue

                    safe_f = self._sanitize_key(f_id)
                    safe_t = self._sanitize_key(t_id)

                    edge_key = self._sanitize_key(f"{safe_f}-{safe_t}-{rel}")
                    if not llm_edges.has(edge_key):
                        try:
                            llm_edges.insert({
                                "_key": edge_key,
                                "_from": f"llm_nodes/{safe_f}",
                                "_to": f"llm_nodes/{safe_t}",
                                "relationship": rel
                            })
                        except Exception as e:
                            logger.warning(f"Failed to insert llm_edge {edge_key}: {e}")

        except Exception as e:
            logger.error(f"Error adding runbook to graph DB: {e}")

    def generate_html_graph(self):
        if not self.db:
            return

        try:
            # Query all nodes and edges
            # For a simple representation, we'll fetch them individually
            
            net = Network(height='750px', width='100%', bgcolor='#222222', font_color='white', directed=True)
            
            # Fetch nodes
            for rb in self.db.collection("runbooks"):
                net.add_node(rb["_id"], label=rb.get("title", rb["_key"]), title=rb.get("summary", ""), color="#ff5722", shape="box")
                
            for cat in self.db.collection("categories"):
                net.add_node(cat["_id"], label=cat.get("name", cat["_key"]), color="#4caf50", shape="ellipse")
                
            for auth in self.db.collection("authors"):
                net.add_node(auth["_id"], label=auth.get("name", auth["_key"]), color="#2196f3", shape="ellipse")
                
            for kw in self.db.collection("keywords"):
                net.add_node(kw["_id"], label=kw.get("name", kw["_key"]), color="#ffc107", shape="dot", size=10)

            # Fetch edges
            for edge in self.db.collection("belongs_to"):
                net.add_edge(edge["_from"], edge["_to"], title="belongs_to", color="#ffffff")
                
            for edge in self.db.collection("authored_by"):
                net.add_edge(edge["_from"], edge["_to"], title="authored_by", color="#ffffff")
                
            for edge in self.db.collection("has_keyword"):
                net.add_edge(edge["_from"], edge["_to"], title="has_keyword", color="#ffffff")
                
            if self.db.has_collection("llm_nodes"):
                for n in self.db.collection("llm_nodes"):
                    # Only add if not already present to avoid duplicate ID errors just in case
                    try:
                        net.add_node(n["_id"], label=n.get("label", n["_key"]), title=n.get("type", ""), color="#9c27b0", shape="dot", size=15)
                    except:
                        pass

            if self.db.has_collection("llm_edges"):
                for e in self.db.collection("llm_edges"):
                    try:
                        net.add_edge(e["_from"], e["_to"], title=e.get("relationship", ""), color="#aaaaaa")
                    except:
                        pass
                        
            # Physics options for better layout
            net.set_options("""
            var options = {
              "physics": {
                "forceAtlas2Based": {
                  "gravitationalConstant": -50,
                  "springLength": 100,
                  "springConstant": 0.08
                },
                "minVelocity": 0.75,
                "solver": "forceAtlas2Based"
              }
            }
            """)
            
            graph_dir = "graph"
            os.makedirs(graph_dir, exist_ok=True)
            
            output_path = os.path.join(graph_dir, "runbook_graph.html")
            net.save_graph(output_path)
            logger.info(f"Graph HTML generated successfully at {output_path}")
            
        except Exception as e:
            logger.error(f"Error generating HTML graph: {e}")
