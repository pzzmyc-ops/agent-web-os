function dropAllConnections(guacamole) {
  return new Promise((resolve) => {
    const conns = Array.from(guacamole.activeConnections.values());
    if (conns.length === 0) {
      resolve(0);
      return;
    }
    let left = conns.length;
    const done = () => {
      left -= 1;
      if (left <= 0) {
        resolve(conns.length);
      }
    };
    conns.forEach((conn) => {
      if (conn.state === conn.STATE_CLOSED || conn.state === conn.STATE_CLOSING) {
        done();
        return;
      }
      conn.once("close", done);
      const g = conn.guacdClient;
      const sock = g && g.guacdConnection;
      if (sock && !sock.destroyed && !sock.closed) {
        g.sendInstruction(["disconnect"]);
        sock.end();
        return;
      }
      conn.close();
    });
  });
}

module.exports = { dropAllConnections };
