import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;
import 'package:flutter_markdown/flutter_markdown.dart';
import 'package:google_fonts/google_fonts.dart';
import 'auth_service.dart';

class ChatScreen extends StatefulWidget {
  const ChatScreen({super.key});

  @override
  State<ChatScreen> createState() => _ChatScreenState();
}

class _ChatScreenState extends State<ChatScreen> {
  final TextEditingController _controller = TextEditingController();
  final ScrollController _scrollController = ScrollController();
  final AuthService _authService = AuthService();
  final List<Map<String, String>> _messages = []; // {'role': 'user'|'agent', 'content': '...'}
  bool _isLoading = false;
  String? _sessionId;

  Future<void> _sendMessage() async {
    final text = _controller.text.trim();
    if (text.isEmpty) return;

    setState(() {
      _messages.add({'role': 'user', 'content': text});
      _isLoading = true;
    });
    _controller.clear();
    _scrollToBottom();

    try {
      // Use localhost:8080. If running in browser, make sure server allows CORS.
      // For Android Emulator use 10.0.2.2, for iOS Simulator localhost.
      // Here we assume Web or macOS Desktop -> localhost is fine.
      final url = Uri.parse('http://localhost:8080/chat');
      
      final body = {
        'message': text,
        if (_sessionId != null) 'session_id': _sessionId,
      };

      final token = await _authService.getIdToken();

      final response = await http.post(
        url,
        headers: {
          'Content-Type': 'application/json',
          if (token != null) 'Authorization': 'Bearer $token',
        },
        body: jsonEncode(body),
      );

      if (response.statusCode == 200) {
        final data = jsonDecode(response.body);
        final agentResponse = data['response'];
        _sessionId = data['session_id'];

        setState(() {
          _messages.add({'role': 'agent', 'content': agentResponse});
        });
      } else {
        // Try to parse custom error message from backend
        String errorMsg = 'Error: ${response.statusCode} - ${response.reasonPhrase}';
        try {
          final errorData = jsonDecode(response.body);
          if (errorData['detail'] != null) {
            errorMsg = errorData['detail'];
          }
        } catch (_) {
          // Keep default message if parsing fails
        }
        
        setState(() {
          _messages.add({'role': 'agent', 'content': errorMsg});
        });
      }
    } catch (e) {
      setState(() {
        _messages.add({'role': 'agent', 'content': 'Connection Error: $e'});
      });
    } finally {
      setState(() {
        _isLoading = false;
      });
      _scrollToBottom();
    }
  }

  void _scrollToBottom() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (_scrollController.hasClients) {
        _scrollController.animateTo(
          _scrollController.position.maxScrollExtent,
          duration: const Duration(milliseconds: 300),
          curve: Curves.easeOut,
        );
      }
    });
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Image.asset('assets/logo.png', height: 32),
            const SizedBox(width: 12),
            const Text('GCE Manager Agent'),
          ],
        ),
        centerTitle: true,
        backgroundColor: Theme.of(context).colorScheme.surface,
        elevation: 0,
        actions: [
          IconButton(
            icon: const Icon(Icons.logout),
            onPressed: () => _authService.signOut(),
            tooltip: 'Sign Out',
          ),
        ],
      ),
      body: Column(
        children: [
          Expanded(
            child: ListView.builder(
              controller: _scrollController,
              padding: const EdgeInsets.all(16),
              itemCount: _messages.length,
              itemBuilder: (context, index) {
                final msg = _messages[index];
                final isUser = msg['role'] == 'user';
                return Align(
                  alignment: isUser ? Alignment.centerRight : Alignment.centerLeft,
                  child: Container(
                    margin: const EdgeInsets.symmetric(vertical: 8),
                    padding: const EdgeInsets.all(12),
                    constraints: BoxConstraints(
                      maxWidth: MediaQuery.of(context).size.width * 0.8,
                    ),
                    decoration: BoxDecoration(
                      color: isUser 
                          ? const Color(0xFF2D2D2D) // Dark Gray for User
                          : Theme.of(context).colorScheme.surface.withOpacity(0.5), // Subtle transparent/surface
                      borderRadius: BorderRadius.circular( isUser ? 16 : 12).copyWith(
                        bottomRight: isUser ? Radius.zero : null,
                        bottomLeft: !isUser ? Radius.zero : null,
                      ),
                      border: isUser ? null : Border.all(color: Colors.grey.withOpacity(0.25), width: 1), // Fine border for card look
                    ),
                    child: isUser
                        ? Text(
                            msg['content']!,
                            style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                              color: Colors.white, // Force white for user text
                              height: 1.4,
                            ),
                          )
                        : MarkdownBody(
                            data: msg['content']!,
                            selectable: true,
                            styleSheet: MarkdownStyleSheet.fromTheme(Theme.of(context)).copyWith(
                              p: Theme.of(context).textTheme.bodyMedium?.copyWith(
                                color: Colors.white, // Force white for report text
                                height: 1.4,
                              ),
                              blockSpacing: 12.0,
                              strong: Theme.of(context).textTheme.bodyMedium?.copyWith(
                                fontWeight: FontWeight.w500,
                                color: Colors.white, // Force white for strong text
                              ),
                              h1: const TextStyle(fontWeight: FontWeight.w500, fontSize: 18, color: Colors.white),
                              h2: const TextStyle(fontWeight: FontWeight.w500, fontSize: 16, color: Colors.white),
                              h3: const TextStyle(fontWeight: FontWeight.w500, fontSize: 14, color: Colors.white),
                              code: Theme.of(context).textTheme.bodyMedium?.copyWith(
                                fontFamily: 'monospace',
                                fontSize: 12, // Code blocks 12px
                                color: Colors.grey[300],
                                backgroundColor: Colors.transparent,
                              ),
                              codeblockDecoration: BoxDecoration(
                                color: const Color(0xFF212121),
                                borderRadius: BorderRadius.circular(8),
                                border: Border.all(color: Colors.white12),
                              ),
                              listBullet: Theme.of(context).textTheme.bodyMedium?.copyWith(
                                color: Colors.white, // Force white for bullets
                              ),
                            ),
                          ),
                  ),
                );
              },
            ),
          ),
          if (_isLoading)
            const Padding(
              padding: EdgeInsets.all(8.0),
              child: LinearProgressIndicator(),
            ),
          Padding(
            padding: const EdgeInsets.all(16.0),
            child: Row(
              children: [
                Expanded(
                  child: TextField(
                    controller: _controller,
                    onSubmitted: (_) => _sendMessage(),
                    decoration: InputDecoration(
                      hintText: 'Ask about your instances...',
                      border: OutlineInputBorder(
                        borderRadius: BorderRadius.circular(24),
                        borderSide: BorderSide.none,
                      ),
                      filled: true,
                      fillColor: Theme.of(context).colorScheme.surfaceContainerHighest,
                      contentPadding: const EdgeInsets.symmetric(
                        horizontal: 20,
                        vertical: 14,
                      ),
                    ),
                  ),
                ),
                const SizedBox(width: 8),
                IconButton.filled(
                  onPressed: _sendMessage,
                  icon: const Icon(Icons.send_rounded),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}
