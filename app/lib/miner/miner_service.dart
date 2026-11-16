import 'dart:async';
import 'dart:convert';
import 'package:flutter/foundation.dart';
import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';
import 'package:web_socket_channel/web_socket_channel.dart';
import '../config.dart';

/// Phone side: reads the gateway stream, stores readings locally,
/// and syncs the backlog to HQ.
class MinerService extends ChangeNotifier {
  double bpm = 0, spo2 = 0;
  String quality = 'offline';
  DateTime _lastDataTime = DateTime.now(); 
  List<double> wave = [];
  bool gatewayUp = false, hqUp = false, deadZone = false;
  int pending = 0;

  final List<Map<String, dynamic>> _queue = [];
  WebSocketChannel? _ch;
  Timer? _syncT;
  DateTime _lastStore = DateTime.fromMillisecondsSinceEpoch(0);
  int _storeCount = 0;
  bool _disposed = false;
  SharedPreferences? _prefs;

  Future<void> start() async {
    _prefs = await SharedPreferences.getInstance();
    final saved = _prefs!.getString('queue');
    if (saved != null) {
      _queue.addAll((jsonDecode(saved) as List).map((e) => Map<String, dynamic>.from(e)));
      pending = _queue.length;
    }
    
    _connect();
    
    // Check for sync AND connection timeouts every 2 seconds
    _syncT = Timer.periodic(const Duration(seconds: 2), (_) {
      _flush();
      
      // If no data for 3 seconds, switch to offline
      if (DateTime.now().difference(_lastDataTime).inSeconds > 3) {
        if (gatewayUp || quality != 'offline') {
          gatewayUp = false;
          quality = 'offline';
          wave = [];
          bpm = 0;
          spo2 = 0;
          notifyListeners();
        }
      }
    });
  }

  Future<void> _connect() async {
    if (_disposed) return;
    try {
      print("Trying to connect to: http://${Config.gateway}/start");
      await http.post(Uri.parse('http://${Config.gateway}/start')).timeout(const Duration(seconds: 4));
      
      print("Starting WebSocket...");
      _ch = WebSocketChannel.connect(Uri.parse('ws://${Config.gateway}/ws'));
      await _ch!.ready;
      gatewayUp = true;
      _lastDataTime = DateTime.now(); // Reset timer on connect
      notifyListeners();
      _ch!.stream.listen(_onData, onDone: _retry, onError: (_) => _retry());
    } catch (e) {
      print("Connection Error: $e");
      _retry();
    }
  }

  void _retry() {
    gatewayUp = false;
    if (_disposed) return;
    notifyListeners();
    Future.delayed(const Duration(seconds: 3), _connect);
  }

  void _onData(dynamic msg) {
    try {
      final j = jsonDecode(msg as String) as Map<String, dynamic>;
      
      // 1. Update last data time
      _lastDataTime = DateTime.now();
      
      // 2. Parse values (default quality to offline just in case)
      final incomingQuality = (j['quality'] as String?) ?? 'offline';
      final incomingBpm = (j['bpm'] as num).toDouble();
      final incomingSpo2 = (j['spo2'] as num).toDouble();
      final incomingWave = (j['filtered_data'] as List).map((e) => (e as num).toDouble()).toList();

      // 3. Handle offline state immediately
      if (incomingQuality == 'offline' || incomingQuality == 'disconnected') {
        quality = 'offline';
        wave = [];
        bpm = 0;
        spo2 = 0;
        notifyListeners();
        return; // Don't save fake zeroes to the queue
      }

      // 4. If it's good data, update state
      quality = incomingQuality;
      bpm = incomingBpm;
      spo2 = incomingSpo2;
      wave = incomingWave;

      // 5. Save to local queue (throttled to 1Hz)
      final now = DateTime.now();
      if (now.difference(_lastStore).inMilliseconds >= 1000) {
        _lastStore = now;
        _queue.add({
          'miner_id': Config.minerId,
          'zone': Config.zone,
          'bpm': bpm,
          'spo2': spo2,
          'quality': quality,
          'ts': now.millisecondsSinceEpoch / 1000.0,
        });
        if (_queue.length > 5000) _queue.removeAt(0);
        pending = _queue.length;
        if (++_storeCount % 5 == 0) _persist();
      }
      
      notifyListeners();
    } catch (e) {
      print("Error parsing JSON: $e");
    }
  }

  void _persist() => _prefs?.setString('queue', jsonEncode(_queue));

  Future<void> _flush() async {
    if (_queue.isEmpty) return;
    if (deadZone) {
      hqUp = false;
      notifyListeners();
      return;
    }
    try {
      final batch = List<Map<String, dynamic>>.from(_queue);
      final r = await http
          .post(Uri.parse('http://${Config.hq}/readings'),
              headers: {'Content-Type': 'application/json'}, body: jsonEncode(batch))
          .timeout(const Duration(seconds: 3));
      if (r.statusCode == 200) {
        _queue.removeRange(0, batch.length);
        hqUp = true;
      } else {
        hqUp = false;
      }
    } catch (_) {
      hqUp = false;
    }
    pending = _queue.length;
    _persist();
    notifyListeners();
  }

  void setDeadZone(bool v) {
    deadZone = v;
    notifyListeners();
  }

  @override
  void dispose() {
    _disposed = true;
    _syncT?.cancel();
    _ch?.sink.close();
    super.dispose();
  }
}