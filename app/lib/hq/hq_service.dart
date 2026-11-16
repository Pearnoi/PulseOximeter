import 'dart:async';
import 'dart:convert';
import 'package:flutter/foundation.dart';
import 'package:http/http.dart' as http;
import 'package:web_socket_channel/web_socket_channel.dart';
import '../config.dart';
import '../models.dart';

class HqService extends ChangeNotifier {
  Snapshot? snap;
  bool connected = false;
  bool _disposed = false;
  WebSocketChannel? _ch;

  Future<void> connect() async {
    if (_disposed) return;
    try {
      _ch = WebSocketChannel.connect(Uri.parse('ws://${Config.hq}/hq'));
      await _ch!.ready;
      connected = true;
      notifyListeners();
      _ch!.stream.listen(
        (m) {
          snap = Snapshot.fromJson(jsonDecode(m as String));
          notifyListeners();
        },
        onDone: _retry,
        onError: (_) => _retry(),
      );
    } catch (_) {
      _retry();
    }
  }

  void _retry() {
    connected = false;
    if (_disposed) return;
    notifyListeners();
    Future.delayed(const Duration(seconds: 2), connect);
  }

  Uri _u(String p) => Uri.parse('http://${Config.hq}$p');
  Future<void> sim(String s) => http.post(_u('/sim/$s'));
  Future<void> ack(int id) => http.post(_u('/alerts/$id/ack'));

  /// Last 15 minutes of readings for one miner (used by the detail screen).
  Future<List<Map<String, dynamic>>> history(String minerId) async {
    final r = await http.get(_u('/miners/$minerId/history')).timeout(const Duration(seconds: 3));
    return (jsonDecode(r.body)['points'] as List).cast<Map<String, dynamic>>();
  }

  @override
  void dispose() {
    _disposed = true;
    _ch?.sink.close();
    super.dispose();
  }
}
