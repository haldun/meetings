// Dropdown menus
$("body").bind("click", function (e) {
  $('.dropdown-toggle, .menu').parent("li").removeClass("open");
});
$(".dropdown-toggle, .menu").click(function (e) {
  var $li = $(this).parent("li").toggleClass('open');
  return false;
});

// Generic tornado stuff
function getCookie(name) {
  var r = document.cookie.match("\\b" + name + "=([^;]*)\\b");
  return r ? r[1] : undefined;
}

// Home page js
(function(){
  if (!$(document.body).hasClass('home')) {
    return;
  }

  $.ajaxSetup({
    data: {_xsrf: getCookie('_xsrf')}
  });

  $('tr.room').delegate('a.danger', 'click', function(e){
    e.preventDefault();
    var self = $(this);
    if (confirm('Are you sure to delete this room? All the messages and files in this room will also be deleted')) {
      $.post("/rooms/" + self.data('room') + "/delete", function(){
        self.parents('tr.room').fadeOut();
      });
    }
  });
})();

// Room page js
(function(){
  if (!window.M) {
    return;
  }

  $('#room-menu a').removeClass('active');
  $('#room-menu a.' + M.active_menu).addClass('active');

  // Sounds
  var sounds = {
    'new': new MediaElement('snd_new'),
    'on': new MediaElement('snd_on'),
    'off': new MediaElement('snd_off')
  };

  var $form = $('#new_message');
  var $compose = $form.find('textarea');

  var messageToDom = function(message) {
    var $tr = $('<tr>');

    $tr.append($('<td>').addClass('user').text(message.user_name));

    var $content = $('<td>');

    if (message.type === 'image') {
      var $a = $('<a>').attr({href: message.url, target: '_blank'});
      $a.append($('<img>').attr('src', message.thumb_url));
      $content.append($a);
    } else if (message.type === 'file') {
      $content.append($('<a>').attr({href: message.url, target: '_blank'}).text(message.url));
    } else if (message.type === 'text') {
      $content.text(message.content);
      // $content.html(linkify(message.content));
    } else if (message.type === 'topic_changed') {
      $content.text('changed topic to ' + message.content);
    } else if (message.type === 'presence') {
      $content.text('entered the room');
    } else if (message.type === 'leave') {
      $content.text('left the room');
    }

    $tr.append($content);

    $('#no-messages').fadeOut('slow');

    return $tr;
  };

  PUBNUB.subscribe({
    channel: M.room.token,
    error: function() {
      // alert("Connection lost.");
    },
    callback: function(message) {
      if (message.type == 'image' || message.type == 'file') {
        $('#messages').append(messageToDom(message));
        scroll_page();
        sounds['new'].play();
      }

      if (message.type == 'text' && message.user_id !== M.current_user.id) {
        $('#messages').append(messageToDom(message));
        scroll_page();
        sounds['new'].play();
      }

      if (message.type == 'presence' && message.user_id !== M.current_user.id) {
        var id = 'user_' + message.user_id;
        if ($('#' + id).length === 0) {
          var el = $('<li>').attr('id', id).text(message.user_name);
          $('#room-users').append(el);
          $('#messages').append(messageToDom(message));
          sounds['on'].play();
        }
      }

      if (message.type === 'leave') {
        $('#user_' + message.user_id).fadeOut('slow').remove();
        $('#messages').append(messageToDom(message));
        sounds['off'].play();
      }

      if (message.type === 'topic_changed') {
        $('#topic').text(message.content);
        $('#messages').append(messageToDom(message));
      }
    },
    connect: function() {}
  });

  $form.submit(function(e){
    var $this = $(this), url = $this.attr('action');
    $('#messages').append(messageToDom({
      user_name: M.current_user.name,
      content: $compose.val(),
      type: 'text'
    }));
    scroll_page();
    $.post(url, $this.serialize(), function(){
    });
    $this[0].reset();
    e.preventDefault();
  });

  $compose.keypress(function(e) {
    var code = (e.keyCode ? e.keyCode : e.which);
    if (code === 13) {
      e.preventDefault();

      if (uploader.total.queued > 0) {
        uploader.start();
        $(this).val('');
        // uploader.files = [];
      } else {
        if ($(this).val() !== '') {
          $form.submit();
        }
      }
    }
  });

  var scroll_page = function() {
    $('html, body').animate({scrollTop: $(document).height()}, 'slow');
  };

  $('#messages').find('tr.text td').each(function(i, el) {
    el.innerHTML = linkify(el.innerHTML);
  });

  if ($('#messages').length > 0) {
    setTimeout(scroll_page, 50);
  }
  $compose.focus();

  var getCookie = function(name) {
    var r = document.cookie.match("\\b" + name + "=([^;]*)\\b");
    return r ? r[1] : undefined;
  };

  function format(){

   var formatted_str = arguments[0] || '';

   for(var i=1; i<arguments.length; i++){
       var re = new RegExp("\\{"+(i-1)+"}", "gim");
       formatted_str = formatted_str.replace(re, arguments[i]);
   }

   return formatted_str;
  };


  function linkify(s){
     var imgexts = [".bmp", ".gif", ".jpg", ".jpeg", ".png", ".tif"];
     var match = s.match(/(https?:\/\/.*(?=&quot;|&#39;))|[^\s\t]{0}(http:\/\/[^\s\t]*)/);

     if (match !== null && match !== undefined){
         var temp = [];
         for (var i=0; i< match.length; i++){
             if(match[i] !== undefined && match[i] !== null &&
                     temp.indexOf(match[i]) === -1){
                 temp.push(match[i]);
             }
         };
         for(var i=0; i< temp.length; i++){
             var item = temp[i];
             var ext = item.match(/\.[^\.]+$/);
             if (ext !== undefined && ext !== null && imgexts.indexOf(ext[0]) !== -1){
                 s = s.replace(item, thumbinize(item));
             }else{
                 if (item.match(/youtube\.com\/(v|watch)/)){
                     s = s.replace(item, youtubeVideoEmbedder(item));
                 }else{
                     s = s.replace(item, format("<a href='{0}' target='_blank'>{0}</a>",
                                          item));
                 }
             }
         }
     };
     return s;
  };


  function thumbinize(s){
   return format('<a href="{0}" target="_blank"><img src="{0}" class="thumbnail" alt="{0}"></a>', s);
  };


  function youtubeVideoEmbedder(url){
   var width = 300;
   var height = 200;

   var part = url.match(/youtube\.com\/watch\?v=([a-zA-Z0-9\-_]+)/);
   if (part){
       url = "http://www.youtube.com/v/" + part[1];
   }

   return format ('<object width="{0}" height="{1}">' +
    '<param name="movie" value="{2}?fs=1"</param>' +
    '<param name="allowFullScreen" value="true"></param>' +
    '<param name="wmode" value="transparent">' +
    '<embed src="{2}?fs=1"' +
    ' type="application/x-shockwave-flash"'+
    ' allowfullscreen="true"' +
    ' width="{0}" height="{1}" wmode="transparent">' +
    '</embed>' +
    '</object>', width, height, url);
  };


  // Uploader
  window.uploader = new plupload.Uploader({
      runtimes: 'html5,flash',
      browse_button: 'select_files',
      container: 'upload_container',
      max_file_size: '10mb',
      url: '/rooms/' + M.room.id + '/upload',
      flash_swf_url: '/static/javascripts/plupload.flash.swf',
      filters: [],
      multipart: true,
      multipart_params: {
        '_xsrf': getCookie('_xsrf'),
        'auth_token': getCookie('auth_token')
      },
      drop_element: 'text_content'
    });

    uploader.bind('Init', function(up, params) {});

    uploader.bind('FilesAdded', function(up, files) {
      var val = $('#text_content').val();
      $.each(files, function(i, file) {
        val += file.name + ' ' + plupload.formatSize(file.size) + '\n';
        // $('#filelist').append(
        //   $('<div>').attr('id', file.id).text(
        //       file.name + ' (' + plupload.formatSize(file.size) + ')')
        //   .append('<b>')
        // );
      });
      $('#text_content').val(val);
      up.refresh();
      $('#upload_buttons').show();
      $('#upload').show();
    });

    uploader.bind('UploadProgress', function(up, file) {
      $('#' + file.id + " b").html(file.percent + "%");
    });

    uploader.bind('Error', function(up, err) {
      $('#filelist').append("<div>Error: " + err.code +
        ", Message: " + err.message +
        (err.file ? ", File: " + err.file.name : "") +
        "</div>"
      );

      up.refresh();
    });

    uploader.bind('FileUploaded', function(up, file) {
      // $('#' + file.id + " b").html("100%");
      $('#upload').text('Upload files').removeClass('disabled').hide();
    });

    $('#upload').click(function(e) {
      uploader.start();
      $compose.val('');
      $(this).text('Uploading...').addClass('disabled');
      e.preventDefault();
    });

    uploader.init();

  $('#room-menu a:not(.leave)').pjax('#content').live('click', function(){
    $('#room-menu a').removeClass('active');
    $(this).addClass('active');
  })

  $(document.body).bind('end.pjax', function(xhr){
    $('#messages').find('tr.text td').each(function(i, el) {
      el.innerHTML = linkify(el.innerHTML);
    });
    $compose.focus();
    if ($('#messages').length > 0) {
      setTimeout(scroll_page, 50);
    }
  });
})();
